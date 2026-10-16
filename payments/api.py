from __future__ import annotations

import logging

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

    # Refund webhooks arrive on the same endpoint — ack for now (RMA later).
    if refund_token and not order_token:
        try:
            montonio.decode_refund_token(refund_token)
        except Exception:
            raise HttpError(400, "Invalid token signature")
        return {"status": "ok", "ignored": "refund"}

    try:
        decoded = montonio.decode_order_token(order_token)
    except Exception:
        raise HttpError(400, "Invalid token signature")

    merchant_reference = str(decoded.get("merchantReference") or "").strip()
    payment_status = str(decoded.get("paymentStatus") or "").strip().upper()
    order_uuid = str(decoded.get("uuid") or "")

    if not merchant_reference.isdigit():
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
