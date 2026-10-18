from __future__ import annotations

import logging
from decimal import Decimal

from django.db import transaction
from django.utils import timezone
from ninja import Router
from ninja.errors import HttpError

from checkout.models import Order, OrderEvent, PaymentIntent
from checkout.services import capture_inventory_for_order
from promotions.services import redeem_coupon_for_paid_order

from . import montonio

logger = logging.getLogger(__name__)

router = Router(tags=["payments"])


@router.post("/montonio/webhook")
def montonio_webhook(request):
    """Montonio Stargate order webhook — verifies the signed orderToken JWT.

    Always answers 200/201 for handled events so Montonio doesn't retry for
    48h; 400 only on a malformed/invalid signature payload.
    """
    import json

    try:
        body = json.loads(request.body or b"{}")
    except Exception:
        raise HttpError(400, "Invalid JSON body")

    order_token = (body.get("orderToken") or "").strip()
    refund_token = (body.get("refundToken") or "").strip()

    if not order_token and not refund_token:
        raise HttpError(400, "Missing orderToken")

    # Refund webhooks arrive on the same endpoint.
    if refund_token and not order_token:
        try:
            decoded_refund = montonio.decode_refund_token(refund_token)
        except Exception:
            raise HttpError(400, "Invalid token signature")
        _handle_refund_webhook(decoded_refund)
        return {"status": "ok"}

    try:
        decoded = montonio.decode_order_token(order_token)
    except Exception:
        raise HttpError(400, "Invalid token signature")

    merchant_reference = str(decoded.get("merchantReference") or "").strip()
    payment_status = str(decoded.get("paymentStatus") or "").strip().upper()
    order_uuid = str(decoded.get("uuid") or "")

    if not merchant_reference.isdigit():
        # Surcharge references look like "{order_id}-sur-{surcharge_id}".
        import re
        m = re.match(r"^(\d+)-sur-(\d+)$", merchant_reference)
        if m:
            _handle_surcharge_webhook(
                surcharge_id=int(m.group(2)),
                payment_status=payment_status,
                order_uuid=order_uuid,
                decoded=decoded,
            )
            return {"status": "ok", "surcharge_id": int(m.group(2))}
        # Not our reference format — ack to stop retries.
        return {"status": "ok", "ignored": "unknown_reference"}

    order_id = int(merchant_reference)

    with transaction.atomic():
        pi = (
            PaymentIntent.objects.select_for_update()
            .select_related("order")
            .filter(order_id=order_id, provider=PaymentIntent.Provider.MONTONIO)
            .first()
        )
        if pi is None:
            return {"status": "ok", "ignored": "no_intent"}

        pi.raw_response = {**(pi.raw_response or {}), "last_webhook": decoded}
        if order_uuid and not pi.external_id:
            pi.external_id = order_uuid

        if pi.status == PaymentIntent.Status.SUCCEEDED:
            pi.save(update_fields=["raw_response", "external_id", "updated_at"])
            return {"status": "ok", "already": "succeeded"}

        if payment_status == "PAID":
            pi.status = PaymentIntent.Status.SUCCEEDED
            pi.save(update_fields=["status", "raw_response", "external_id", "updated_at"])
            if pi.order.status != Order.Status.PAID:
                pi.order.status = Order.Status.PAID
                pi.order.save(update_fields=["status", "updated_at"])
            capture_inventory_for_order(order_id=pi.order_id)
            redeem_coupon_for_paid_order(order_id=pi.order_id)
            result = {"status": "ok", "order_id": order_id, "marked": "paid"}
        elif payment_status in {"VOIDED", "ABANDONED", "FAILED"}:
            pi.status = PaymentIntent.Status.FAILED
            pi.save(update_fields=["status", "raw_response", "external_id", "updated_at"])
            result = {"status": "ok", "order_id": order_id, "marked": "failed"}
        else:
            pi.save(update_fields=["raw_response", "external_id", "updated_at"])
            result = {"status": "ok", "order_id": order_id, "payment_status": payment_status}

    try:
        OrderEvent.objects.create(
            order_id=order_id,
            kind=OrderEvent.Kind.PAYMENT,
            name="montonio_webhook",
            payload={
                "payment_status": payment_status,
                "uuid": order_uuid,
                "provider_name": decoded.get("paymentProviderName"),
                "sender_iban": decoded.get("senderIban"),
                "at": timezone.now().isoformat(),
            },
        )
    except Exception:
        pass

    return result


def _handle_refund_webhook(decoded: dict) -> None:
    """Apply a refundToken webhook to the matching refund surcharge.

    Token fields: refundUuid, refundStatus, refundStatusDescription,
    refundAmount, orderUuid. A refund with no matching surcharge (issued
    from the Montonio dashboard) gets a resolved surcharge row so the
    order balance stays truthful.
    """
    from checkout.models import Surcharge

    refund_uuid = str(decoded.get("refundUuid") or "").strip()
    refund_status = str(decoded.get("refundStatus") or "").strip().upper()
    description = str(decoded.get("refundStatusDescription") or "")
    order_uuid = str(decoded.get("orderUuid") or "").strip()

    s = None
    if refund_uuid:
        s = Surcharge.objects.filter(
            external_id=refund_uuid,
            direction=Surcharge.Direction.REFUND,
        ).select_related("order").first()
    if s is None:
        # Refund created outside ops (Montonio dashboard) — find the order
        # via the original payment intent and record a resolved row.
        pi = PaymentIntent.objects.filter(external_id=order_uuid).first()
        if pi is None:
            logger.warning("refund webhook: no order for %s", order_uuid)
            return
        try:
            amount = Decimal(str(decoded.get("refundAmount") or 0))
        except Exception:
            amount = Decimal("0")
        status = (
            Surcharge.Status.RESOLVED if refund_status == "SUCCESSFUL"
            else Surcharge.Status.FAILED if refund_status == "REJECTED"
            else Surcharge.Status.PENDING
        )
        s = Surcharge.objects.create(
            order_id=pi.order_id,
            direction=Surcharge.Direction.REFUND,
            source=Surcharge.Source.MANUAL,
            method=Surcharge.Method.MONTONIO,
            status=status,
            amount_gross=amount,
            currency=pi.currency or "EUR",
            external_id=refund_uuid,
            reason="Montonio dashboard refund",
            raw_response={"refund_webhook": decoded},
            paid_at=timezone.now() if status == Surcharge.Status.RESOLVED else None,
        )
        return

    with transaction.atomic():
        s = Surcharge.objects.select_for_update().get(id=s.id)
        s.raw_response = {**(s.raw_response or {}), "refund_webhook": decoded}
        update = ["raw_response", "updated_at"]
        if refund_status == "SUCCESSFUL" and s.status != Surcharge.Status.RESOLVED:
            s.status = Surcharge.Status.RESOLVED
            s.paid_at = timezone.now()
            update += ["status", "paid_at"]
        elif refund_status == "REJECTED":
            s.status = Surcharge.Status.FAILED
            update += ["status"]
        elif refund_status == "CANCELED":
            # Refund cancelled before processing — obligation stays open,
            # clear the provider link so it can be submitted again.
            s.status = Surcharge.Status.PENDING
            s.external_id = ""
            update += ["status", "external_id"]
        s.save(update_fields=update)

    try:
        OrderEvent.objects.create(
            order_id=s.order_id,
            kind=OrderEvent.Kind.PAYMENT,
            name="montonio_refund_webhook",
            payload={
                "surcharge_id": s.id,
                "refund_uuid": refund_uuid,
                "refund_status": refund_status,
                "description": description,
                "at": timezone.now().isoformat(),
            },
        )
    except Exception:
        pass


def _handle_surcharge_webhook(*, surcharge_id: int, payment_status: str,
                              order_uuid: str, decoded: dict) -> None:
    from checkout.models import Surcharge

    with transaction.atomic():
        s = (
            Surcharge.objects.select_for_update()
            .filter(id=surcharge_id, method=Surcharge.Method.MONTONIO)
            .first()
        )
        if s is None:
            return
        s.raw_response = {**(s.raw_response or {}), "last_webhook": decoded}
        if order_uuid and not s.external_id:
            s.external_id = order_uuid
        if s.status == Surcharge.Status.PAID:
            s.save(update_fields=["raw_response", "external_id", "updated_at"])
            return
        if payment_status == "PAID":
            s.status = Surcharge.Status.PAID
            s.paid_at = timezone.now()
            s.save(update_fields=[
                "status", "paid_at", "raw_response", "external_id", "updated_at"])
        elif payment_status in {"VOIDED", "ABANDONED", "FAILED"}:
            s.status = Surcharge.Status.FAILED
            s.save(update_fields=[
                "status", "raw_response", "external_id", "updated_at"])
        else:
            s.save(update_fields=["raw_response", "external_id", "updated_at"])
