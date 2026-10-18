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

from accounts.auth import JWTAuth

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


@router.post("/inbank/callback")
def inbank_callback(request):
    """Inbank e-POS server-to-server callback (form-urlencoded).

    Fields: message (urlencoded JSON {uuid, status, purchase_reference}),
    timestamp, hmac (SHA512 over "{timestamp}.{message}" with the API key).
    """
    import json

    from payments import inbank as inbank_svc

    message = (request.POST.get("message") or "").strip()
    timestamp = (request.POST.get("timestamp") or "").strip()
    hmac_value = (request.POST.get("hmac") or "").strip()

    if not inbank_svc.verify_callback(
            timestamp=timestamp, message=message, hmac_value=hmac_value):
        raise HttpError(400, "Invalid callback signature")

    try:
        decoded = json.loads(message)
    except Exception:
        raise HttpError(400, "Invalid callback message")

    session_uuid = str(decoded.get("uuid") or "").strip()
    session_status = str(decoded.get("status") or "").strip().lower()
    purchase_ref = str(decoded.get("purchase_reference") or "").strip()

    pi = None
    if session_uuid:
        pi = (
            PaymentIntent.objects.select_related("order")
            .filter(
                external_id=session_uuid,
                provider=PaymentIntent.Provider.INBANK,
            )
            .first()
        )
    if pi is None and purchase_ref.isdigit():
        pi = (
            PaymentIntent.objects.select_related("order")
            .filter(
                order_id=int(purchase_ref),
                provider=PaymentIntent.Provider.INBANK,
            )
            .first()
        )
    if pi is None:
        logger.warning("inbank callback: no intent for %s/%s",
                       session_uuid, purchase_ref)
        return {"status": "ok", "ignored": "no_intent"}

    result = {"status": "ok"}
    with transaction.atomic():
        pi = PaymentIntent.objects.select_for_update().get(id=pi.id)
        pi.raw_response = {
            **(pi.raw_response or {}), "last_callback": decoded}
        update = ["raw_response", "updated_at"]

        if pi.status == PaymentIntent.Status.SUCCEEDED:
            pi.save(update_fields=update)
            return {"status": "ok", "already": "succeeded"}

        if session_status == "completed":
            # Contract activated — merchant is paid; fulfil the order.
            pi.status = PaymentIntent.Status.SUCCEEDED
            pi.save(update_fields=["status"] + update)
            if pi.order.status != Order.Status.PAID:
                pi.order.status = Order.Status.PAID
                pi.order.save(update_fields=["status", "updated_at"])
            capture_inventory_for_order(order_id=pi.order_id)
            redeem_coupon_for_paid_order(order_id=pi.order_id)
            result = {"status": "ok", "order_id": pi.order_id,
                      "marked": "paid"}
        elif session_status == "cancelled":
            pi.status = PaymentIntent.Status.CANCELLED
            pi.save(update_fields=["status"] + update)
            result = {"status": "ok", "order_id": pi.order_id,
                      "marked": "cancelled"}
        elif session_status in {"declined", "failed", "expired"}:
            pi.status = PaymentIntent.Status.FAILED
            pi.save(update_fields=["status"] + update)
            result = {"status": "ok", "order_id": pi.order_id,
                      "marked": "failed"}
        else:
            # granted/positive/unsigned/signed/income_proof_required/…
            pi.save(update_fields=update)
            result = {"status": "ok", "order_id": pi.order_id,
                      "session_status": session_status}

    try:
        OrderEvent.objects.create(
            order_id=pi.order_id,
            kind=OrderEvent.Kind.PAYMENT,
            name="inbank_callback",
            payload={
                "session_uuid": session_uuid,
                "session_status": session_status,
                "at": timezone.now().isoformat(),
            },
        )
    except Exception:
        pass

    return result


@router.get("/inbank/calculation", auth=JWTAuth())
def inbank_calculation(request, amount: str, period: int = 12):
    """Server-side proxy for the Inbank installment calculator.

    The API key stays on the backend — the storefront calls this to show
    'nuo X €/mėn'. Returns the simple response level.
    """
    if not getattr(request, "auth", None):
        raise HttpError(401, "Unauthorized")

    from payments import inbank as inbank_svc

    if not inbank_svc.is_configured():
        raise HttpError(503, "Inbank is not configured")
    try:
        amt = Decimal(str(amount))
    except Exception:
        raise HttpError(400, "Invalid amount")
    if amt <= 0:
        raise HttpError(400, "Invalid amount")
    period = max(1, min(int(period or 12), 84))

    try:
        body = inbank_svc.calculate(amount=amt, period=period)
    except inbank_svc.InbankError as exc:
        raise HttpError(502, str(exc))

    return {
        "amount": body.get("amount"),
        "period": body.get("period"),
        "currency": body.get("currency", "EUR"),
        "monthly_payment": body.get("paymentAmountMonthly"),
        "interest_rate_annual": body.get("interestRateAnnual"),
        "credit_cost_rate_annual": body.get("creditCostRateAnnual"),
        "total_cost": body.get("totalCost"),
        "total_cost_of_credit": body.get("totalCostOfCredit"),
        "down_payment_minimum_amount": body.get(
            "downPaymentMinimumAmount"),
    }
