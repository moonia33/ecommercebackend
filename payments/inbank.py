"""Inbank e-POS (redirect) integration.

Docs: https://docs.inbank.eu/api/epos-flow

Auth: Bearer API key. Shop identified by UUID in the URL path.
Callbacks are form-urlencoded posts (message, hmac, timestamp); the HMAC
is SHA512 over "{timestamp}.{message}" keyed by the API key.

Settings:
    INBANK_API_URL          https://demo-api.inbank.lt/partner/v3 (prod:
                            https://api.inbank.lt/partner/v3)
    INBANK_API_KEY          secret Bearer key
    INBANK_SHOP_UUID        shop UUID for URL paths
    INBANK_PRODUCT_CODE     product reference from Inbank
    INBANK_LOCALE           lt-LT (default)
    INBANK_MERCHANT_DOMAIN  storefront domain (e.g. shop.example.lt)
    INBANK_RETURN_URL / INBANK_CANCEL_URL / INBANK_CALLBACK_URL
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
from dataclasses import dataclass
from decimal import Decimal
from urllib.parse import urljoin

import requests
from django.conf import settings

logger = logging.getLogger(__name__)


class InbankError(Exception):
    pass


def api_url() -> str:
    return (
        getattr(settings, "INBANK_API_URL", "")
        or "https://demo-api.inbank.lt/partner/v3"
    ).rstrip("/") + "/"


def api_key() -> str:
    return (getattr(settings, "INBANK_API_KEY", "") or "").strip()


def shop_uuid() -> str:
    return (getattr(settings, "INBANK_SHOP_UUID", "") or "").strip()


def product_code() -> str:
    return (getattr(settings, "INBANK_PRODUCT_CODE", "") or "").strip()


def is_configured() -> bool:
    return bool(api_key() and shop_uuid() and product_code())


def _headers() -> dict:
    return {
        "Authorization": f"Bearer {api_key()}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


def _post(path: str, payload: dict) -> dict:
    url = urljoin(api_url(), path)
    try:
        resp = requests.post(
            url, headers=_headers(), data=json.dumps(payload), timeout=30)
    except requests.RequestException as exc:
        raise InbankError(f"Inbank request failed: {exc}") from exc
    if resp.status_code >= 400:
        raise InbankError(f"Inbank {resp.status_code}: {resp.text[:300]}")
    try:
        return resp.json()
    except ValueError as exc:
        raise InbankError("Inbank returned non-JSON response") from exc


def _get(path: str) -> dict:
    url = urljoin(api_url(), path)
    try:
        resp = requests.get(url, headers=_headers(), timeout=30)
    except requests.RequestException as exc:
        raise InbankError(f"Inbank request failed: {exc}") from exc
    if resp.status_code >= 400:
        raise InbankError(f"Inbank {resp.status_code}: {resp.text[:300]}")
    return resp.json()


@dataclass
class PosSession:
    uuid: str
    redirect_url: str
    status: str
    raw: dict


def create_pos_session(*, order, locale: str = "lt-LT",
                       product_code_override: str | None = None) -> PosSession:
    """POST /shops/{uuid}/pos-sessions -> customer redirect to e-POS."""
    site = (getattr(settings, "INBANK_MERCHANT_DOMAIN", "") or "").strip()
    if not site:
        site = (getattr(settings, "STOREFRONT_URL", "") or "").strip()
    site = site.rstrip("/")

    items = []
    for ln in order.lines.all():
        items.append({
            "itemReference": str(ln.variant.sku if ln.variant_id else ln.id),
            "type": "product",
            "description": str(ln.name)[:200],
            "quantity": str(int(ln.qty)),
            "amount": str(ln.line_gross),
        })
    if order.shipping_gross and Decimal(order.shipping_gross) > 0:
        items.append({
            "type": "service",
            "description": "Pristatymas",
            "quantity": "1",
            "amount": str(order.shipping_gross),
        })

    email = ""
    try:
        email = (order.user.email or "").strip()
    except Exception:
        pass

    payload = {
        "general": {
            "productCode": (
                (product_code_override or "").strip() or product_code()),
            "totalAmount": float(order.total_gross),
            "currency": (order.currency or "EUR").upper(),
            "locale": (getattr(settings, "INBANK_LOCALE", "") or locale
                       or "lt-LT"),
        },
        "origin": {"value": "redirect_integration"},
        "purchase": {
            "purchaseReference": str(order.id),
            "description": f"Užsakymas #{order.id}",
            "items": items,
            "merchant": {"merchantDomainName": site},
        },
        "partnerUrls": {
            "returnUrl": getattr(settings, "INBANK_RETURN_URL", ""),
            "cancelUrl": getattr(settings, "INBANK_CANCEL_URL", ""),
            "callbackUrl": getattr(settings, "INBANK_CALLBACK_URL", ""),
        },
        "integrationInfo": {
            "module": "django-ecommerce",
            "callbackMode": "standard",
        },
    }
    if email:
        payload["customerContactData"] = {"email": email}

    body = _post(f"shops/{shop_uuid()}/pos-sessions", payload)
    return PosSession(
        uuid=str(body.get("uuid") or ""),
        redirect_url=str(body.get("redirectUrl") or ""),
        status=str(body.get("status") or ""),
        raw=body,
    )


def calculate(*, amount, period: int, down_payment_amount=None) -> dict:
    """POST /shops/{uuid}/calculations — monthly payment preview.

    Server-side proxy: the API key must never reach the browser.
    """
    payload = {
        "productCode": product_code(),
        "amount": float(amount),
        "period": int(period),
        "currency": "EUR",
        "responseLevel": "simple",
    }
    if down_payment_amount is not None:
        payload["downPaymentAmount"] = float(down_payment_amount)
    return _post(f"shops/{shop_uuid()}/calculations", payload)


def get_pos_session(session_uuid: str) -> dict:
    """GET /shops/{uuid}/pos-sessions/{sessionUuid}."""
    return _get(f"shops/{shop_uuid()}/pos-sessions/{session_uuid}")


def get_products() -> list:
    """GET /shops/{uuid}/products — product setup incl. amount bounds."""
    body = _get(f"shops/{shop_uuid()}/products")
    return body if isinstance(body, list) else body.get("products", [])


def get_products_cached(*, ttl: int = 300) -> list:
    """Cached + failure-tolerant get_products for checkout rendering."""
    if not is_configured():
        return []
    from django.core.cache import cache

    key = f"inbank:products:{shop_uuid()}:{api_url()}"
    cached = cache.get(key)
    if cached is not None:
        return cached
    try:
        products = get_products()
    except InbankError as exc:
        logger.warning("Inbank products fetch failed: %s", exc)
        return []
    cache.set(key, products, ttl)
    return products


def verify_callback(*, timestamp: str, message: str, hmac_value: str) -> bool:
    """HMAC-SHA512 over '{timestamp}.{message}' keyed by the API key."""
    if not (timestamp and message and hmac_value and api_key()):
        return False
    data = f"{timestamp}.{message}".encode("utf-8")
    expected = hmac.new(api_key().encode("utf-8"), data,
                        hashlib.sha512).hexdigest()
    return hmac.compare_digest(expected, hmac_value.strip().lower())
