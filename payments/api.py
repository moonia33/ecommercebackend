from __future__ import annotations

import logging

from django.db import transaction
from ninja import Router
from ninja.errors import HttpError

from .schemas import NeopayBankOut, NeopayCallbackIn, NeopayCountryOut
from .services.neopay import decode_neopay_token, get_neopay_config


router = Router(tags=["payments"])

logger = logging.getLogger(__name__)


@router.post("/neopay/callback")
def neopay_callback(request, payload: NeopayCallbackIn):
    token = (payload.token or "").strip()
    if not token:
        raise HttpError(400, "Missing token")

    decoded = decode_neopay_token(token)
    transactions = decoded.get("transactions")

    # Neopay may send either:
    # 1) server-side callback token: { transactions: { <txId>: { status, bank, ... } } }
    # 2) client redirect token: { transactionId, status, bank, ... }
    if transactions is None:
        tx_id = (decoded.get("transactionId") or "").strip()
        if not tx_id:
            raise HttpError(400, "Invalid token payload")
        info = {
            "status": decoded.get("status"),
            "action": decoded.get("action"),
            "bank": decoded.get("bank"),
        }
        transactions = {tx_id: info}
    elif not isinstance(transactions, dict):
        raise HttpError(400, "Invalid token payload")

    from checkout.models import PaymentIntent
    from checkout.services import capture_inventory_for_order, release_inventory_for_order
    from promotions.services import redeem_coupon_for_paid_order, release_coupon_for_order

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
                if getattr(pi, "order", None) is not None:
                    try:
                        pi.order.status = pi.order.Status.PAID
                        pi.order.save(update_fields=["status", "updated_at"])
                        capture_inventory_for_order(order_id=pi.order.id)
                        redeem_coupon_for_paid_order(order_id=pi.order.id)
                    except Exception:
                        logger.exception("Failed to finalize PAID order (capture inventory / redeem coupon)", extra={"order_id": getattr(pi.order, "id", None), "tx_id": str(tx_id)})
            elif status in ["failed", "rejected", "error"]:
                pi.status = PaymentIntent.Status.FAILED
                try:
                    if getattr(pi, "order", None) is not None:
                        pi.order.status = pi.order.Status.CANCELLED
                        pi.order.save(update_fields=["status", "updated_at"])
                        release_coupon_for_order(order_id=pi.order.id)
                        release_inventory_for_order(order_id=pi.order.id)
                except Exception:
                    logger.exception("Failed to cancel failed order and release inventory", extra={"order_id": getattr(getattr(pi, "order", None), "id", None), "tx_id": str(tx_id)})
            elif status in ["canceled", "cancelled"]:
                pi.status = PaymentIntent.Status.CANCELLED
                try:
                    if getattr(pi, "order", None) is not None:
                        pi.order.status = pi.order.Status.CANCELLED
                        pi.order.save(update_fields=["status", "updated_at"])
                        release_coupon_for_order(order_id=pi.order.id)
                        release_inventory_for_order(order_id=pi.order.id)
                except Exception:
                    logger.exception("Failed to cancel cancelled order and release inventory", extra={"order_id": getattr(getattr(pi, "order", None), "id", None), "tx_id": str(tx_id)})
            elif status in [
                "signed",
                "pending",
                "started",
                "unknown",
                "partially signed",
                "partially_signed",
            ]:
                pi.status = PaymentIntent.Status.PENDING

            pi.save(update_fields=["status", "raw_response", "neopay_bank_bic", "neopay_bank_name", "updated_at"])

    return {"status": "success"}


@router.get("/neopay/banks", response=list[NeopayBankOut])
def neopay_banks(request, country_code: str = "LT"):
    cfg = get_neopay_config()
    if not cfg:
        raise HttpError(400, "Neopay config is not set")

    if not cfg.enable_bank_preselect:
        return []

    forced_bic = (getattr(cfg, "force_bank_bic", "") or "").strip()
    forced_name = (getattr(cfg, "force_bank_name", "") or "").strip()
    if forced_bic:
        cc = (country_code or "").strip().upper() or "LT"
        return [
            NeopayBankOut(
                country_code=cc,
                bic=forced_bic,
                name=forced_name or forced_bic,
                service_types=["pisp"],
            )
        ]

    cc = (country_code or "").strip().upper() or "LT"

    import requests

    base = (cfg.banks_api_base_url or "https://psd2.neopay.lt/api").rstrip("/")
    if base.endswith("/countries"):
        base = base[: -len("/countries")]
    candidates = [
        f"{base}/countries/{cfg.project_id}",
        f"{base}/countries/{cfg.project_id}/",
        # Fallback: some environments expose only the generic countries list.
        f"{base}/countries",
        f"{base}/countries/",
    ]
    # Some environments/document versions use base without '/api'.
    if base.endswith("/api"):
        root = base[: -len("/api")]
        candidates.append(f"{root}/api/countries/{cfg.project_id}")
        candidates.append(f"{root}/api/countries/{cfg.project_id}/")

    last_response = None
    last_exc: Exception | None = None

    for url in candidates:
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
            last_exc = e
            continue

        last_response = r
        if r.status_code == 404:
            continue
        if r.status_code >= 400:
            body = (r.text or "").strip().replace("\n", " ")
            if len(body) > 300:
                body = body[:300] + "..."
            raise HttpError(502, f"Neopay banks api failed: {r.status_code} url={url} body={body}")

        data = r.json()
        break
    else:
        if last_exc is not None:
            raise HttpError(502, f"Neopay banks api request failed: {type(last_exc).__name__}")
        if last_response is not None:
            body = (last_response.text or "").strip().replace("\n", " ")
            if len(body) > 300:
                body = body[:300] + "..."
            raise HttpError(
                502,
                f"Neopay banks api failed: {last_response.status_code} url={candidates[-1]} body={body}",
            )
        raise HttpError(502, "Neopay banks api failed: no response")

    # 'data' is set from the first successful candidate above.

    # If we hit the generic endpoint, normalize to the same shape we expect.
    if isinstance(data, list):
        data = {"countries": data}

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
            logo_url = (b.get("logo") or b.get("logoUrl") or "").strip() if isinstance(b.get("logo") or b.get("logoUrl") or "", str) else ""
            is_operating = bool(b.get("isOperating")) if "isOperating" in b else True
            if bic:
                result.append(
                    NeopayBankOut(
                        country_code=ccode or cc,
                        bic=bic,
                        name=name or bic,
                        service_types=[str(x) for x in services if str(x).strip()],
                        logo_url=logo_url,
                        is_operating=is_operating,
                    )
                )

    return result


@router.get("/neopay/countries", response=list[NeopayCountryOut])
def neopay_countries(request, country_code: str | None = None):
    cfg = get_neopay_config()
    if not cfg:
        raise HttpError(400, "Neopay config is not set")

    if not cfg.enable_bank_preselect:
        return []

    import requests

    base = (cfg.banks_api_base_url or "https://psd2.neopay.lt/api").rstrip("/")
    if base.endswith("/countries"):
        base = base[: -len("/countries")]
    candidates = [
        f"{base}/countries/{cfg.project_id}",
        f"{base}/countries/{cfg.project_id}/",
        # Fallback: some environments expose only the generic countries list.
        f"{base}/countries",
        f"{base}/countries/",
    ]
    if base.endswith("/api"):
        root = base[: -len("/api")]
        candidates.append(f"{root}/api/countries/{cfg.project_id}")
        candidates.append(f"{root}/api/countries/{cfg.project_id}/")

    last_response = None
    last_exc: Exception | None = None

    for url in candidates:
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
            last_exc = e
            continue

        last_response = r
        if r.status_code == 404:
            continue
        if r.status_code >= 400:
            body = (r.text or "").strip().replace("\n", " ")
            if len(body) > 300:
                body = body[:300] + "..."
            raise HttpError(502, f"Neopay banks api failed: {r.status_code} url={url} body={body}")

        data = r.json()
        break
    else:
        if last_exc is not None:
            raise HttpError(502, f"Neopay banks api request failed: {type(last_exc).__name__}")
        if last_response is not None:
            body = (last_response.text or "").strip().replace("\n", " ")
            if len(body) > 300:
                body = body[:300] + "..."
            raise HttpError(
                502,
                f"Neopay banks api failed: {last_response.status_code} url={candidates[-1]} body={body}",
            )
        raise HttpError(502, "Neopay banks api failed: no response")

    if isinstance(data, list):
        countries = data
    elif isinstance(data, dict):
        countries = data.get("countries") or []
        # Some shapes might be keyed by country code.
        if not countries and country_code:
            cc = (country_code or "").strip().upper()
            if cc and cc in data and isinstance(data.get(cc), dict):
                countries = [{"code": cc, **data.get(cc)}]
    else:
        countries = []

    cc_filter = (country_code or "").strip().upper() if country_code else ""

    out: list[NeopayCountryOut] = []
    for c in countries:
        if not isinstance(c, dict):
            continue
        code = (c.get("code") or c.get("country") or c.get("countryCode") or "").strip().upper()
        if cc_filter and code != cc_filter:
            continue
        name = (c.get("name") or c.get("countryName") or "").strip()
        currency = (c.get("currency") or "").strip()
        default_language = (c.get("defaultLanguage") or c.get("defaultLocale") or "").strip().upper()
        languages = c.get("languages") or []
        if isinstance(languages, str):
            languages = [languages]
        if not isinstance(languages, list):
            languages = []
        rules = c.get("rules") or {}
        if not isinstance(rules, dict):
            rules = {}

        banks = c.get("aspsps") or c.get("banks") or []
        if not isinstance(banks, list):
            banks = []

        banks_out: list[NeopayBankOut] = []
        for b in banks:
            if not isinstance(b, dict):
                continue
            bic = (b.get("bic") or b.get("BIC") or "").strip()
            bname = (b.get("name") or b.get("bankName") or "").strip()
            services = b.get("services") or b.get("serviceTypes") or []
            if isinstance(services, str):
                services = [services]
            if not isinstance(services, list):
                services = []
            logo_url = (b.get("logo") or b.get("logoUrl") or "").strip() if isinstance(b.get("logo") or b.get("logoUrl") or "", str) else ""
            is_operating = bool(b.get("isOperating")) if "isOperating" in b else True
            if not bic:
                continue
            banks_out.append(
                NeopayBankOut(
                    country_code=code or cc_filter or "",
                    bic=bic,
                    name=bname or bic,
                    service_types=[str(x) for x in services if str(x).strip()],
                    logo_url=logo_url,
                    is_operating=is_operating,
                )
            )

        out.append(
            NeopayCountryOut(
                code=code,
                name=name,
                currency=currency,
                default_language=default_language,
                languages=[str(x).strip().upper() for x in languages if str(x).strip()],
                rules={str(k).strip().upper(): str(v) for k, v in rules.items() if str(k).strip() and str(v).strip()},
                aspsps=banks_out,
            )
        )

    return out
