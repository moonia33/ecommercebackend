from __future__ import annotations

from django.db import transaction
from ninja import Router
from ninja.errors import HttpError

from .schemas import NeopayBankOut, NeopayCallbackIn
from .services.neopay import decode_neopay_token, get_neopay_config


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

            bank = info.get("bank") or {}
            bank_bic = (bank.get("bic") or "").strip() if isinstance(bank, dict) else ""
            bank_name = (bank.get("name") or "").strip() if isinstance(bank, dict) else ""

            pi.raw_response = {
                **(pi.raw_response or {}),
                "neopay_callback": decoded,
                "last_callback_tx": str(tx_id),
                "last_callback_status": status,
                "last_callback_action": action,
            }

            if bank_bic:
                pi.neopay_bank_bic = bank_bic
            if bank_name:
                pi.neopay_bank_name = bank_name

            if status == "success":
                pi.status = PaymentIntent.Status.SUCCEEDED
            elif status in ["failed", "rejected", "error"]:
                pi.status = PaymentIntent.Status.FAILED
            elif status in ["canceled", "cancelled"]:
                pi.status = PaymentIntent.Status.CANCELLED

            pi.save(update_fields=["status", "raw_response", "neopay_bank_bic", "neopay_bank_name", "updated_at"])

    return {"status": "success"}


@router.get("/neopay/banks", response=list[NeopayBankOut])
def neopay_banks(request, country_code: str = "LT"):
    cfg = get_neopay_config()
    if not cfg:
        raise HttpError(400, "Neopay config is not set")

    if not cfg.enable_bank_preselect:
        return []

    cc = (country_code or "").strip().upper() or "LT"

    import requests

    base = (cfg.banks_api_base_url or "https://psd2.neopay.lt/api").rstrip("/")
    url = f"{base}/countries/{cfg.project_id}"
    try:
        r = requests.get(
            url,
            timeout=20,
            headers={
                "Accept": "application/json",
                "User-Agent": "inultimo-backend/1.0",
            },
        )
    except requests.RequestException as e:
        raise HttpError(502, f"Neopay banks api request failed: {type(e).__name__}")

    if r.status_code >= 400:
        body = (r.text or "").strip().replace("\n", " ")
        if len(body) > 300:
            body = body[:300] + "..."
        raise HttpError(
            502,
            f"Neopay banks api failed: {r.status_code} url={url} body={body}",
        )

    data = r.json()

    # Best-effort parsing; Neopay may change response shape.
    countries = None
    if isinstance(data, dict):
        countries = data.get("countries")
        if countries is None and cc in data and isinstance(data.get(cc), dict):
            countries = [{"country": cc, **data.get(cc)}]

    if not countries:
        return []

    result: list[NeopayBankOut] = []
    for c in countries:
        if not isinstance(c, dict):
            continue
        ccode = (c.get("country") or c.get("code") or c.get("countryCode") or "").strip().upper()
        if ccode and ccode != cc:
            continue
        banks = c.get("banks") or c.get("aspsps") or []
        if not isinstance(banks, list):
            continue
        for b in banks:
            if not isinstance(b, dict):
                continue
            bic = (b.get("bic") or b.get("BIC") or "").strip()
            name = (b.get("name") or b.get("bankName") or "").strip()
            services = b.get("services") or b.get("serviceTypes") or []
            if isinstance(services, str):
                services = [services]
            if not isinstance(services, list):
                services = []
            if bic:
                result.append(
                    NeopayBankOut(
                        country_code=ccode or cc,
                        bic=bic,
                        name=name or bic,
                        service_types=[str(x) for x in services if str(x).strip()],
                    )
                )

    return result
