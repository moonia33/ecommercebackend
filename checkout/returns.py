"""RMA (return merchandise authorization) domain logic.

Lifecycle: pending -> approved -> received -> completed (credit issued).
Rejected/cancelled are terminal; approved can be cancelled while nothing
was received.

Amounts are never computed here for money movement — the credit invoice
mirrors the issued sales invoice; the refund itself stays manual.
"""

from __future__ import annotations

from decimal import Decimal

from django.conf import settings
from django.db import models, transaction
from django.utils import timezone

from .models import OrderLine, ReturnLine, ReturnRequest, Shipment


def _default_return_days() -> int:
    return int(getattr(settings, "DEFAULT_RETURN_DAYS", 14))


def line_return_days(order_line: OrderLine) -> int:
    """Effective return window for a line (product override or default)."""
    product = getattr(getattr(order_line, "variant", None), "product", None)
    days = getattr(product, "return_days", None)
    return int(days) if days else _default_return_days()


def line_is_returnable(order_line: OrderLine) -> bool:
    product = getattr(getattr(order_line, "variant", None), "product", None)
    return bool(getattr(product, "returnable", True))


def returned_qty_map(order) -> dict[int, int]:
    """order_line_id -> qty already on non-rejected/non-cancelled returns."""
    out: dict[int, int] = {}
    qs = ReturnLine.objects.filter(
        return_request__order=order,
    ).exclude(
        return_request__status__in=[
            ReturnRequest.Status.REJECTED, ReturnRequest.Status.CANCELLED]
    ).values_list("order_line_id", "qty")
    for lid, qty in qs:
        out[lid] = out.get(lid, 0) + int(qty)
    return out


@transaction.atomic
def create_return(
    order,
    *,
    lines: list[dict],
    note: str = "",
    approve: bool = True,
    allow_expired: bool = False,
    user=None,
) -> ReturnRequest:
    """Create an RMA for order lines.

    lines: [{"order_line_id": int, "qty": int, "reason"?: str}].
    Hard-blocks non-returnable products; blocks expired return windows
    unless allow_expired (manager discretion). Cumulative qty across
    open returns can never exceed the ordered qty.
    """
    ret = ReturnRequest.objects.create(
        order=order,
        number="",
        note=note or "",
        status=(ReturnRequest.Status.APPROVED if approve
                else ReturnRequest.Status.PENDING),
        decided_at=timezone.now() if approve else None,
        created_by=user,
    )
    ret.number = f"RMA-{ret.id:05d}"
    ret.save(update_fields=["number"])

    already = returned_qty_map(order)
    delivered_at = order.delivered_at
    today = timezone.localdate()

    if not lines:
        raise ValueError("No return lines")
    for item in lines:
        ol = order.lines.filter(id=int(item.get("order_line_id") or 0)).first()
        if ol is None:
            raise ValueError("Order line not found")
        qty = int(item.get("qty") or 0)
        if qty <= 0:
            continue
        if not line_is_returnable(ol):
            raise ValueError(f"'{ol.name}' is not returnable")
        if already.get(ol.id, 0) + qty > int(ol.qty):
            raise ValueError(
                f"'{ol.name}': return qty would exceed ordered "
                f"({already.get(ol.id, 0) + qty} > {ol.qty})")
        if delivered_at and not allow_expired:
            deadline = (delivered_at.date()
                        if hasattr(delivered_at, "date") else delivered_at)
            days = line_return_days(ol)
            if (today - deadline).days > days:
                raise ValueError(
                    f"'{ol.name}': return window ({days}d) has expired; "
                    "pass allow_expired to override")
        ReturnLine.objects.create(
            return_request=ret,
            order_line=ol,
            qty=qty,
            reason=(item.get("reason") or "").strip(),
        )

    if not ret.lines.exists():
        raise ValueError("No valid return lines")
    return ret


def _get_line(ret: ReturnRequest, line_id: int) -> ReturnLine:
    rl = ret.lines.filter(id=int(line_id)).first()
    if rl is None:
        raise ValueError("Return line not found")
    return rl


@transaction.atomic
def set_return_status(
    ret: ReturnRequest, status: str, *, user=None
) -> ReturnRequest:
    s = ReturnRequest.Status
    allowed = {
        s.PENDING: {s.APPROVED, s.REJECTED, s.CANCELLED},
        s.APPROVED: {s.CANCELLED, s.RECEIVED},
        s.RECEIVED: {s.COMPLETED},
        s.REJECTED: set(),
        s.COMPLETED: set(),
        s.CANCELLED: set(),
    }
    if status not in allowed.get(ret.status, set()):
        raise ValueError(f"Cannot move {ret.status} -> {status}")
    ret.status = status
    fields = ["status", "updated_at"]
    now = timezone.now()
    if status in {s.APPROVED, s.REJECTED} and ret.decided_at is None:
        ret.decided_at = now
        fields.append("decided_at")
    if status == s.RECEIVED and ret.received_at is None:
        ret.received_at = now
        fields.append("received_at")
    if status == s.COMPLETED and ret.completed_at is None:
        ret.completed_at = now
        fields.append("completed_at")
    ret.save(update_fields=fields)
    return ret


def send_return_instructions(ret: ReturnRequest) -> bool:
    """Email the customer packing/shipping instructions for an approved RMA.

    Best-effort: a missing template logs a failed OutboundEmail instead of
    raising, so approval never breaks on notification issues.
    """
    from notifications.services import send_templated_email

    order = ret.order
    to_email = getattr(getattr(order, "user", None), "email", "") or ""
    if not to_email:
        return False
    shop = getattr(order, "shop", None)
    lines_text = "\n".join(
        f"- {rl.order_line.name or rl.order_line.sku} x{rl.qty}"
        for rl in ret.lines.select_related("order_line").all())
    res = send_templated_email(
        template_key="return_instructions",
        to_email=to_email,
        language_code=getattr(shop, "default_language_code", "") or "lt",
        context={
            "return_number": ret.number,
            "order_id": order.id,
            "lines": lines_text,
            "tracking_number": (
                ret.shipment.tracking_number if ret.shipment_id else ""),
        },
    )
    return res.ok


@transaction.atomic
def create_return_shipment(
    ret: ReturnRequest,
    *,
    carrier_code: str = "",
    tracking_number: str = "",
    notes: str = "",
    user=None,
) -> Shipment:
    if ret.status not in {ReturnRequest.Status.APPROVED,
                          ReturnRequest.Status.RECEIVED}:
        raise ValueError("Shipment can only be added to an approved return")
    sh = Shipment.objects.create(
        order=ret.order,
        kind=Shipment.Kind.RETURN,
        status=Shipment.Status.PENDING,
        carrier_code=(carrier_code or "").strip(),
        tracking_number=(tracking_number or "").strip(),
        notes=notes or "",
        created_by=user,
    )
    ret.shipment = sh
    ret.save(update_fields=["shipment", "updated_at"])
    return sh


@transaction.atomic
def receive_return(
    ret: ReturnRequest,
    *,
    items: list[dict],
    restock_warehouse_id: int | None = None,
    user=None,
) -> ReturnRequest:
    """Mark goods as received: [{return_line_id, qty}].

    Optionally restocks each received qty into the given warehouse's
    InventoryItem (goods physically back on the shelf).
    """
    if ret.status not in {ReturnRequest.Status.APPROVED,
                          ReturnRequest.Status.RECEIVED}:
        raise ValueError("Return must be approved before receiving goods")

    item = None
    if restock_warehouse_id:
        from catalog.models import InventoryItem, Warehouse
        wh = Warehouse.objects.filter(
            id=restock_warehouse_id, is_active=True).first()
        if wh is None:
            raise ValueError("Warehouse not found")

    for it in items:
        rl = _get_line(ret, int(it.get("return_line_id") or 0))
        qty = int(it.get("qty") or 0)
        if qty <= 0:
            continue
        if rl.received_qty + qty > rl.qty:
            raise ValueError(
                f"Received qty would exceed requested "
                f"({rl.received_qty + qty} > {rl.qty})")
        rl.received_qty += qty
        rl.save(update_fields=["received_qty"])

        if restock_warehouse_id and rl.order_line.variant_id:
            inv, _ = InventoryItem.objects.get_or_create(
                variant=rl.order_line.variant,
                warehouse_id=restock_warehouse_id,
                defaults={"qty_on_hand": 0, "qty_reserved": 0},
            )
            InventoryItem.objects.filter(id=inv.id).update(
                qty_on_hand=models.F("qty_on_hand") + qty)

    if (ret.status != ReturnRequest.Status.RECEIVED
            and ret.lines.exists() and all(
                rl.received_qty >= rl.qty for rl in ret.lines.all())):
        set_return_status(ret, ReturnRequest.Status.RECEIVED, user=user)
    elif ret.received_at is None:
        ret.received_at = timezone.now()
        ret.save(update_fields=["received_at", "updated_at"])
    return ret


@transaction.atomic
def credit_return(
    ret: ReturnRequest,
    *,
    discount_gross=None,
    send_email: bool = False,
    user=None,
) -> object:
    """Issue a credit invoice for everything received but not yet credited."""
    from billing.models import Invoice, InvoiceLine
    from billing.services import issue_credit_invoice, send_invoice_email

    if ret.status not in {ReturnRequest.Status.RECEIVED,
                          ReturnRequest.Status.COMPLETED}:
        raise ValueError("Goods must be received before crediting")

    invoice = Invoice.objects.filter(
        order=ret.order, kind=Invoice.Kind.SALES,
        status=Invoice.Status.ISSUED).first()
    if invoice is None:
        raise ValueError("Issue the sales invoice before crediting a return")

    inv_lines = {
        ln.order_line_id: ln
        for ln in InvoiceLine.objects.filter(
            invoice=invoice, order_line__isnull=False)
    }

    credit_lines = []
    pending: list[tuple[ReturnLine, int]] = []
    for rl in ret.lines.select_related("order_line").all():
        qty = int(rl.received_qty) - int(rl.credited_qty)
        if qty <= 0:
            continue
        il = inv_lines.get(rl.order_line_id)
        if il is None:
            # Legacy invoices predate the order_line FK — fall back to an
            # unambiguous name match and persist it for future credits.
            matches = [
                cand for cand in invoice.lines.all()
                if cand.order_line_id is None
                and cand.name == rl.order_line.name
            ]
            if len(matches) > 1:
                matches = [
                    cand for cand in matches if int(cand.qty) == int(rl.qty)]
            if len(matches) == 1:
                il = matches[0]
                il.order_line = rl.order_line
                il.save(update_fields=["order_line"])
        if il is None:
            raise ValueError(
                f"'{rl.order_line.name}': no invoice line to credit")
        credit_lines.append({"invoice_line_id": il.id, "qty": qty})
        pending.append((rl, qty))
    if not credit_lines and discount_gross is None:
        raise ValueError("Nothing received left to credit")

    credit = issue_credit_invoice(
        invoice, lines=credit_lines, discount_gross=discount_gross)

    # The credit reduces what the customer owes — reconcile creates the
    # pending refund row (settled via Montonio or manually).
    from checkout.surcharges import reconcile_order_balance
    reconcile_order_balance(
        order=ret.order, reason=f"Kreditinė {credit.number} (RMA {ret.number})",
        user=user)

    for rl, qty in pending:
        rl.credited_qty += qty
        rl.save(update_fields=["credited_qty"])

    ret.credit_invoice = credit
    ret.save(update_fields=["credit_invoice", "updated_at"])
    if (ret.status != ReturnRequest.Status.COMPLETED
            and all(rl.credited_qty >= rl.qty for rl in ret.lines.all())):
        set_return_status(ret, ReturnRequest.Status.COMPLETED, user=user)

    if send_email:
        send_invoice_email(credit)
    return credit
