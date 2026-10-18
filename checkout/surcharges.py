from __future__ import annotations

from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from checkout.models import Order, OrderEvent, PaymentIntent, Surcharge
from payments import montonio


def bank_transfer_instructions(*, order: Order) -> str:
    from checkout.api.shared import _bank_transfer_instructions_for

    return _bank_transfer_instructions_for(
        order_id=order.id, country_code=order.country_code)


def credited_amount_gross(*, order: Order) -> Decimal:
    """Absolute gross credited back via issued credit invoices."""
    from billing.models import Invoice

    total = Decimal("0.00")
    qs = Invoice.objects.filter(
        order=order, kind=Invoice.Kind.CREDIT,
        status=Invoice.Status.ISSUED)
    for inv in qs.only("totals"):
        total += abs(Decimal(str((inv.totals or {}).get("gross") or 0)))
    return total


def paid_amount_gross(*, order: Order) -> Decimal:
    """Everything the customer has actually paid: succeeded payment intents
    plus paid surcharge charges, minus resolved refunds."""
    paid = Decimal("0.00")
    for pi in PaymentIntent.objects.filter(
        order=order, status=PaymentIntent.Status.SUCCEEDED
    ):
        paid += Decimal(pi.amount_gross or 0)
    for s in order.surcharges.filter(status=Surcharge.Status.PAID):
        if s.direction == Surcharge.Direction.CHARGE:
            paid += Decimal(s.amount_gross)
        else:
            paid -= Decimal(s.amount_gross)
    for s in order.surcharges.filter(
        status=Surcharge.Status.RESOLVED,
        direction=Surcharge.Direction.REFUND,
    ):
        paid -= Decimal(s.amount_gross)
    return paid


def refund_in_flight_gross(*, order: Order) -> Decimal:
    """Refunds submitted to the provider but not yet confirmed — pending
    refund rows that already carry an external (provider) id."""
    total = Decimal("0.00")
    for s in order.surcharges.filter(
        status=Surcharge.Status.PENDING,
        direction=Surcharge.Direction.REFUND,
    ).exclude(external_id=""):
        total += Decimal(s.amount_gross or 0)
    return total


def balance_due(*, order: Order) -> Decimal:
    """Positive -> customer owes us; negative -> we owe the customer.

    The effective order total subtracts issued credit notes — a credit
    reduces what the customer owes even though Order.total_gross stays
    immutable.
    """
    effective_total = (
        Decimal(order.total_gross or 0) - credited_amount_gross(order=order))
    return (effective_total - paid_amount_gross(order=order)).quantize(
        Decimal("0.01"))


@transaction.atomic
def reconcile_order_balance(*, order: Order, reason: str = "",
                            user=None) -> Surcharge | None:
    """Maintain a single auto pending balance row for a paid order.

    Called after any change that moves total_gross (line add/remove,
    shipping change). Creates/updates/cancels the pending auto Surcharge;
    never touches manual or already-collected rows. Does nothing until a
    payment has actually been confirmed.
    """
    # Only a confirmed payment establishes the baseline. A manually-set
    # PAID status without a payment record cannot tell us what was
    # actually collected, so we never auto-charge in that case.
    has_payment = PaymentIntent.objects.filter(
        order=order, status=PaymentIntent.Status.SUCCEEDED
    ).exists()
    if not has_payment:
        return None

    due = balance_due(order=order)

    pending_charge = (
        order.surcharges.filter(
            direction=Surcharge.Direction.CHARGE,
            source=Surcharge.Source.AUTO,
            status=Surcharge.Status.PENDING,
        ).order_by("-id").first()
    )
    # Pending refund rows already submitted to the provider (external_id
    # set) are in-flight — their amount is locked. The adjustable pending
    # row is the latest auto row without a provider id.
    in_flight = refund_in_flight_gross(order=order)
    pending_refund = (
        order.surcharges.filter(
            direction=Surcharge.Direction.REFUND,
            source=Surcharge.Source.AUTO,
            status=Surcharge.Status.PENDING,
            external_id="",
        ).order_by("-id").first()
    )

    # Zero out the pending rows contribution when recomputing the target
    # amount (they are pending, not yet paid, so balance_due ignores them).
    result = None

    if due > 0:
        if pending_refund is not None:
            pending_refund.status = Surcharge.Status.CANCELLED
            pending_refund.save(update_fields=["status", "updated_at"])
        if pending_charge is None:
            pending_charge = Surcharge.objects.create(
                order=order,
                direction=Surcharge.Direction.CHARGE,
                source=Surcharge.Source.AUTO,
                amount_gross=due,
                currency=order.currency or "EUR",
                reason=reason,
                created_by=user,
            )
        else:
            amount_changed = pending_charge.amount_gross != due
            pending_charge.amount_gross = due
            if reason:
                pending_charge.reason = reason
            pending_charge.save(update_fields=["amount_gross", "reason", "updated_at"])
            if amount_changed and pending_charge.method == Surcharge.Method.MONTONIO:
                # Montonio links are fixed-amount — regenerate.
                try:
                    _create_montonio_link(
                        pending_charge, language_code=None)
                except Exception:
                    pass
        result = pending_charge
    elif due < 0:
        if pending_charge is not None:
            pending_charge.status = Surcharge.Status.CANCELLED
            pending_charge.save(update_fields=["status", "updated_at"])
        # In-flight refunds already cover part of what we owe — the
        # adjustable pending row only carries the remainder.
        refund_amount = -due - in_flight
        if refund_amount <= 0:
            if pending_refund is not None:
                pending_refund.status = Surcharge.Status.CANCELLED
                pending_refund.save(update_fields=["status", "updated_at"])
                pending_refund = None
            result = None
        elif pending_refund is None:
            pending_refund = Surcharge.objects.create(
                order=order,
                direction=Surcharge.Direction.REFUND,
                source=Surcharge.Source.AUTO,
                amount_gross=refund_amount,
                currency=order.currency or "EUR",
                reason=reason,
                created_by=user,
            )
        else:
            pending_refund.amount_gross = refund_amount
            if reason:
                pending_refund.reason = reason
            pending_refund.save(update_fields=["amount_gross", "reason", "updated_at"])
        if pending_refund is not None:
            result = pending_refund
    else:
        for s in (pending_charge, pending_refund):
            if s is not None:
                s.status = Surcharge.Status.CANCELLED
                s.save(update_fields=["status", "updated_at"])

    if result is not None:
        OrderEvent.objects.create(
            order=order,
            kind=OrderEvent.Kind.PAYMENT,
            name="ops_balance_reconciled",
            actor_user=user,
            payload={
                "balance_due": str(due),
                "surcharge_id": result.id,
                "direction": result.direction,
                "reason": reason,
            },
        )
    return result


def create_surcharge(
    *,
    order: Order,
    amount_gross: Decimal | float | str,
    reason: str = "",
    method: str,
    language_code: str | None = None,
    user=None,
    send_email: bool = True,
) -> Surcharge:
    """Manual surcharge creation (auto rows come from reconcile_order_balance)."""
    amount = Decimal(str(amount_gross)).quantize(Decimal("0.01"))
    if amount <= 0:
        raise ValueError("Surcharge amount must be positive")

    with transaction.atomic():
        s = Surcharge.objects.create(
            order=order,
            direction=Surcharge.Direction.CHARGE,
            source=Surcharge.Source.MANUAL,
            amount_gross=amount,
            currency=order.currency or "EUR",
            reason=(reason or "").strip(),
            method=method,
            created_by=user,
        )
        if method == Surcharge.Method.MONTONIO:
            _create_montonio_link(s, language_code=language_code)

    OrderEvent.objects.create(
        order=order,
        kind=OrderEvent.Kind.PAYMENT,
        name="ops_surcharge_created",
        actor_user=user,
        payload={
            "surcharge_id": s.id,
            "amount_gross": str(s.amount_gross),
            "method": s.method,
            "reason": s.reason,
        },
    )
    if send_email:
        send_surcharge_email(surcharge=s, language_code=language_code)
    return s


def _create_montonio_link(s: Surcharge, *, language_code: str | None = None) -> None:
    result = montonio.create_order_payment(
        order=s.order,
        language_code=language_code,
        amount_override=s.amount_gross,
        merchant_reference=s.reference,
        description=s.reason or f"Order {s.order_id} surcharge",
    )
    s.payment_url = result.payment_url
    s.external_id = result.uuid
    s.raw_response = result.raw_response
    s.save(update_fields=["payment_url", "external_id", "raw_response"])


def collect_surcharge(
    *,
    surcharge: Surcharge,
    method: str,
    language_code: str | None = None,
    user=None,
    send_email: bool = True,
) -> Surcharge:
    """Pick the collection channel for a pending charge and notify customer.

    montonio -> generate the payment link (amount already set).
    bank_transfer -> email carries the configured PaymentMethod instructions.
    Refund-direction rows are resolved manually (no customer email).
    """
    if surcharge.status != Surcharge.Status.PENDING:
        raise ValueError("Surcharge is not pending")
    if surcharge.direction == Surcharge.Direction.REFUND:
        raise ValueError("Refunds are resolved manually, not collected")

    if method not in {Surcharge.Method.MONTONIO, Surcharge.Method.BANK_TRANSFER}:
        raise ValueError("method must be montonio|bank_transfer")

    surcharge.method = method
    surcharge.save(update_fields=["method", "updated_at"])
    if method == Surcharge.Method.MONTONIO and not surcharge.payment_url:
        _create_montonio_link(surcharge, language_code=language_code)

    if send_email:
        send_surcharge_email(surcharge=surcharge, language_code=language_code)

    OrderEvent.objects.create(
        order=surcharge.order,
        kind=OrderEvent.Kind.PAYMENT,
        name="ops_surcharge_sent",
        actor_user=user,
        payload={
            "surcharge_id": surcharge.id,
            "method": surcharge.method,
            "amount_gross": str(surcharge.amount_gross),
        },
    )
    return surcharge


def mark_surcharge_paid(*, surcharge: Surcharge, user=None) -> Surcharge:
    surcharge.status = Surcharge.Status.PAID
    surcharge.paid_at = timezone.now()
    surcharge.save(update_fields=["status", "paid_at", "updated_at"])
    OrderEvent.objects.create(
        order=surcharge.order,
        kind=OrderEvent.Kind.PAYMENT,
        name="ops_surcharge_paid",
        actor_user=user,
        payload={"surcharge_id": surcharge.id},
    )
    return surcharge


def cancel_surcharge(*, surcharge: Surcharge, user=None) -> Surcharge:
    if (surcharge.direction == Surcharge.Direction.REFUND
            and surcharge.status == Surcharge.Status.PENDING
            and surcharge.external_id):
        raise ValueError(
            "Refund already submitted to the provider — wait for the "
            "webhook or resolve manually after it settles")
    surcharge.status = Surcharge.Status.CANCELLED
    surcharge.save(update_fields=["status", "updated_at"])
    OrderEvent.objects.create(
        order=surcharge.order,
        kind=OrderEvent.Kind.PAYMENT,
        name="ops_surcharge_cancelled",
        actor_user=user,
        payload={"surcharge_id": surcharge.id},
    )
    return surcharge


def resolve_refund(*, surcharge: Surcharge, user=None) -> Surcharge:
    """Mark a pending refund row as resolved (money returned manually)."""
    if surcharge.direction != Surcharge.Direction.REFUND:
        raise ValueError("Only refund surcharges can be resolved")
    surcharge.status = Surcharge.Status.RESOLVED
    surcharge.save(update_fields=["status", "updated_at"])
    OrderEvent.objects.create(
        order=surcharge.order,
        kind=OrderEvent.Kind.PAYMENT,
        name="ops_refund_resolved",
        actor_user=user,
        payload={"surcharge_id": surcharge.id},
    )
    return surcharge


def send_surcharge_email(*, surcharge: Surcharge,
                         language_code: str | None = None) -> bool:
    from notifications.services import send_templated_email

    order = surcharge.order
    context = {
        "order_id": order.id,
        "surcharge_id": surcharge.id,
        "amount": f"{surcharge.amount_gross} {surcharge.currency}",
        "reason": surcharge.reason,
        "payment_url": surcharge.payment_url,
        "bank_instructions": (
            bank_transfer_instructions(order=order)
            if surcharge.method == Surcharge.Method.BANK_TRANSFER
            else ""
        ),
    }
    res = send_templated_email(
        template_key="order_surcharge",
        to_email=getattr(order.user, "email", "") or "",
        context=context,
        language_code=language_code
        or getattr(order.shop, "default_language_code", "")
        or "lt",
    )
    if res.ok:
        surcharge.emailed_at = timezone.now()
        surcharge.save(update_fields=["emailed_at", "updated_at"])
    return res.ok


@transaction.atomic
def request_montonio_refund(*, surcharge: Surcharge, user=None) -> Surcharge:
    """Submit a pending refund surcharge to Montonio.

    Locks the row (external_id = refund UUID) and leaves it PENDING until
    the refundToken webhook confirms SUCCESSFUL -> RESOLVED. The original
    Montonio order UUID comes from the succeeded PaymentIntent.
    """
    if surcharge.direction != Surcharge.Direction.REFUND:
        raise ValueError("Only refund surcharges can be refunded")
    if surcharge.status != Surcharge.Status.PENDING:
        raise ValueError("Surcharge is not pending")
    if surcharge.external_id:
        raise ValueError("Refund already submitted to the provider")

    order = surcharge.order
    pi = PaymentIntent.objects.filter(
        order=order,
        provider=PaymentIntent.Provider.MONTONIO,
        status=PaymentIntent.Status.SUCCEEDED,
    ).first()
    if pi is None:
        raise ValueError("Order has no succeeded Montonio payment")
    order_uuid = (pi.external_id or "").strip()
    if not order_uuid:
        raise ValueError(
            "Montonio order UUID missing — the payment webhook has not "
            "arrived yet; try again later")
    if Decimal(surcharge.amount_gross) < Decimal("0.05"):
        raise ValueError("Refund amount below the 0.05 minimum")

    # Idempotency: reuse the same key on retries of this row.
    raw = dict(surcharge.raw_response or {})
    idem = (raw.get("idempotency_key") or "").strip()
    if not idem:
        import uuid
        idem = str(uuid.uuid4())
        raw["idempotency_key"] = idem

    body = montonio.create_refund(
        order_uuid=order_uuid,
        amount=surcharge.amount_gross,
        idempotency_key=idem,
    )
    raw["refund_response"] = body
    raw["order_uuid"] = order_uuid

    refund_uuid = str(body.get("uuid") or body.get("refundUuid") or "").strip()
    surcharge.external_id = refund_uuid or idem
    surcharge.method = Surcharge.Method.MONTONIO
    surcharge.raw_response = raw
    surcharge.save(update_fields=[
        "external_id", "method", "raw_response", "updated_at"])

    OrderEvent.objects.create(
        order=order,
        kind=OrderEvent.Kind.PAYMENT,
        name="ops_refund_submitted",
        actor_user=user,
        payload={
            "surcharge_id": surcharge.id,
            "amount_gross": str(surcharge.amount_gross),
            "order_uuid": order_uuid,
            "refund_uuid": surcharge.external_id,
        },
    )
    return surcharge
