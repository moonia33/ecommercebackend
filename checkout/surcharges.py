from __future__ import annotations

from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from checkout.models import Order, OrderEvent, Surcharge
from payments import montonio


def bank_transfer_instructions(*, order: Order) -> str:
    from checkout.api.shared import _bank_transfer_instructions_for

    return _bank_transfer_instructions_for(
        order_id=order.id, country_code=order.country_code)


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
    """Create an additional charge on an order.

    method=montonio -> Montonio payment link with a custom amount and a
    "NNN-sur-M" merchant reference (webhook marks it paid automatically).
    method=bank_transfer -> instructions from the configured PaymentMethod.
    """
    amount = Decimal(str(amount_gross)).quantize(Decimal("0.01"))
    if amount <= 0:
        raise ValueError("Surcharge amount must be positive")

    with transaction.atomic():
        s = Surcharge.objects.create(
            order=order,
            amount_gross=amount,
            currency=order.currency or "EUR",
            reason=(reason or "").strip(),
            method=method,
            created_by=user,
        )

        if method == Surcharge.Method.MONTONIO:
            result = montonio.create_order_payment(
                order=order,
                language_code=language_code,
                amount_override=amount,
                merchant_reference=s.reference,
                description=reason or f"Order {order.id} surcharge",
            )
            s.payment_url = result.payment_url
            s.external_id = result.uuid
            s.raw_response = result.raw_response
            s.save(update_fields=["payment_url", "external_id", "raw_response"])

    OrderEvent.objects.create(
        order=order,
        kind=OrderEvent.Kind.PAYMENT,
        name="ops_surcharge_created",
        payload={
            "surcharge_id": s.id,
            "amount_gross": str(s.amount_gross),
            "method": s.method,
            "reason": s.reason,
            "user": getattr(user, "email", "") or "",
        },
    )

    if send_email:
        send_surcharge_email(surcharge=s, language_code=language_code)

    return s


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
