from __future__ import annotations

from django.db import transaction
from ninja import Router
from ninja.errors import HttpError

from .schemas import NeopayCallbackIn
from .services.neopay import decode_neopay_token


router = Router(tags=["payments"])


@router.post("/neopay/callback")
def neopay_callback(request, payload: NeopayCallbackIn):
    token = (payload.token or "").strip()
    if not token:
        raise HttpError(400, "Missing token")

    decoded = decode_neopay_token(token)
    transactions = decoded.get("transactions") or {}
    if not isinstance(transactions, dict):
        raise HttpError(400, "Invalid token payload")

    from checkout.models import PaymentIntent

    with transaction.atomic():
        for tx_id, info in transactions.items():
            if not tx_id:
                continue
            if not isinstance(info, dict):
                continue

            pi = (
                PaymentIntent.objects.select_related("order")
                .filter(provider=PaymentIntent.Provider.NEOPAY, external_id=str(tx_id))
                .first()
            )
            if not pi:
                continue

            status = (info.get("status") or "").strip().lower()
            action = (info.get("action") or "").strip().lower()

            pi.raw_response = {
                **(pi.raw_response or {}),
                "neopay_callback": decoded,
                "last_callback_tx": str(tx_id),
                "last_callback_status": status,
                "last_callback_action": action,
            }

            if status == "success":
                pi.status = PaymentIntent.Status.SUCCEEDED
            elif status in ["failed", "rejected", "error"]:
                pi.status = PaymentIntent.Status.FAILED
            elif status in ["canceled", "cancelled"]:
                pi.status = PaymentIntent.Status.CANCELLED

            pi.save(update_fields=["status", "raw_response", "updated_at"])

    return {"status": "success"}
