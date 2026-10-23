from __future__ import annotations

import logging
from dataclasses import dataclass
from decimal import Decimal

import jwt
import urllib.request
import json

from django.conf import settings

logger = logging.getLogger(__name__)

API_URL_SANDBOX = "https://sandbox-stargate.montonio.com/api"
API_URL_PRODUCTION = "https://stargate.montonio.com/api"


class MontonioError(Exception):
    pass


def is_configured() -> bool:
    return bool(access_key() and secret_key())


def access_key() -> str:
    return (getattr(settings, "MONTONIO_ACCESS_KEY", "") or "").strip()


def secret_key() -> str:
    return (getattr(settings, "MONTONIO_SECRET_KEY", "") or "").strip()


def api_url() -> str:
    return (
        getattr(settings, "MONTONIO_API_URL", "") or API_URL_SANDBOX
    ).rstrip("/")


def return_url() -> str:
    return (getattr(settings, "MONTONIO_RETURN_URL", "") or "").strip()


def notification_url() -> str:
    return (getattr(settings, "MONTONIO_NOTIFICATION_URL", "") or "").strip()


@dataclass(frozen=True)
class MontonioOrderResult:
    uuid: str
    payment_url: str
    raw_response: dict


def _address_payload(order) -> dict:
    """Build billing/shipping address from the order's shipping snapshot."""
    full_name = (order.shipping_full_name or "").strip()
    parts = full_name.split(" ", 1)
    first = parts[0] if parts else ""
    last = parts[1] if len(parts) > 1 else ""
    return {
        "firstName": first or "Customer",
        "lastName": last or "",
        "email": getattr(order.user, "email", "") or "",
        "addressLine1": (order.shipping_line1 or "")[:100],
        "locality": (order.shipping_city or "")[:50],
        "region": (order.shipping_city or "")[:50],
        "country": (order.shipping_country_code or order.country_code or "LT"),
        "postalCode": (order.shipping_postal_code or "")[:16],
    }


def _auth_token() -> str:
    """Bearer JWT for GET endpoints ({accessKey, exp})."""
    import time

    token = jwt.encode(
        {"accessKey": access_key(), "exp": int(time.time()) + 600},
        secret_key(), algorithm="HS256")
    return token.decode("utf-8") if isinstance(token, bytes) else token


def get_payment_methods(*, timeout: int = 10) -> dict:
    """GET /stores/payment-methods — enabled methods + bank lists per region.

    Cached for 5 min (Django cache); bank lists change rarely and this is
    called on every checkout payment-step render.
    """
    if not is_configured():
        return {}
    from django.core.cache import cache

    cache_key = f"montonio:payment-methods:{access_key()[:8]}:{api_url()}"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    req = urllib.request.Request(
        f"{api_url()}/stores/payment-methods",
        headers={"Authorization": f"Bearer {_auth_token()}"},
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except Exception as exc:
        logger.warning("Montonio payment-methods fetch failed: %s", exc)
        return {}
    if not isinstance(body, dict):
        return {}
    cache.set(cache_key, body, 300)
    return body


def build_order_payload(*, order, language_code: str | None = None,
                        amount_override: Decimal | None = None,
                        merchant_reference: str | None = None,
                        description: str | None = None,
                        preferred_provider: str | None = None,
                        method: str = "paymentInitiation") -> dict:
    """Montonio Stargate order payload (JWT claims)."""
    grand_total = Decimal(
        amount_override if amount_override is not None else order.total_gross
    )
    locale = (language_code or getattr(settings, "MONTONIO_LOCALE", "lt") or "lt").lower()[:2]

    if amount_override is not None:
        line_items = [
            {
                "name": (description or f"Order {order.id} surcharge")[:128],
                "quantity": 1,
                "finalPrice": float(grand_total),
            }
        ]
    else:
        line_items = [
            {
                "name": (ln.name or ln.sku or "")[:128],
                "quantity": int(ln.qty),
                "finalPrice": float(Decimal(ln.total_gross)),
            }
            for ln in order.lines.all()
        ]
        if order.shipping_gross and Decimal(order.shipping_gross) > 0:
            line_items.append(
                {
                    "name": "Shipping",
                    "quantity": 1,
                    "finalPrice": float(Decimal(order.shipping_gross)),
                }
            )

    merchant_reference = merchant_reference or str(order.id)

    return {
        "accessKey": access_key(),
        "merchantReference": merchant_reference,
        "merchantReferenceDisplay": merchant_reference,
        "returnUrl": return_url(),
        "notificationUrl": notification_url(),
        "currency": order.currency or "EUR",
        "grandTotal": float(grand_total),
        "locale": locale,
        "billingAddress": _address_payload(order),
        "shippingAddress": _address_payload(order),
        "lineItems": line_items,
        "payment": {
            "method": method or "paymentInitiation",
            "methodDisplay": "Pay with your bank",
            "methodOptions": {
                "paymentDescription": description or f"Order {merchant_reference}",
                "preferredCountry": (order.country_code or "LT"),
                "preferredLocale": locale,
                **({"preferredProvider": preferred_provider}
                   if preferred_provider else {}),
            },
            "amount": float(grand_total),
            "currency": order.currency or "EUR",
        },
    }


def create_order_payment(*, order, language_code: str | None = None,
                         amount_override: Decimal | None = None,
                         merchant_reference: str | None = None,
                         description: str | None = None,
                         preferred_provider: str | None = None,
                         method: str = "paymentInitiation",
                         timeout: int = 20) -> MontonioOrderResult:
    """Create a Montonio Stargate order and return the redirect URL."""
    if not is_configured():
        raise MontonioError("Montonio is not configured (missing access/secret key)")

    payload = build_order_payload(
        order=order,
        language_code=language_code,
        amount_override=amount_override,
        merchant_reference=merchant_reference,
        description=description,
        preferred_provider=preferred_provider,
        method=method,
    )
    token = jwt.encode(payload, secret_key(), algorithm="HS256")
    # PyJWT may return bytes on old versions
    if isinstance(token, bytes):
        token = token.decode("utf-8")

    req = urllib.request.Request(
        f"{api_url()}/orders",
        data=json.dumps({"data": token}).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except Exception as exc:
        raise MontonioError(f"Montonio order create failed: {exc}") from exc

    payment_url = (body.get("paymentUrl") or "").strip()
    if not payment_url:
        raise MontonioError(f"Montonio returned no paymentUrl: {body!r}")

    return MontonioOrderResult(
        uuid=(body.get("uuid") or ""),
        payment_url=payment_url,
        raw_response=body,
    )


def create_refund(*, order_uuid: str, amount, idempotency_key: str,
                  timeout: int = 20) -> dict:
    """Create a refund against a paid Stargate order.

    POST /refunds with a JWT {accessKey, orderUuid, amount, idempotencyKey,
    iat, exp}. The caller owns persistence of the idempotency key so a
    retry submits the identical request.
    """
    import time

    if not is_configured():
        raise MontonioError(
            "Montonio is not configured (missing access/secret key)")
    order_uuid = (order_uuid or "").strip()
    if not order_uuid:
        raise MontonioError("Missing Montonio order UUID")
    amt = Decimal(str(amount)).quantize(Decimal("0.01"))
    if amt < Decimal("0.05"):
        raise MontonioError("Refund amount below the 0.05 minimum")

    now = int(time.time())
    payload = {
        "accessKey": access_key(),
        "orderUuid": order_uuid,
        "amount": float(amt),
        "idempotencyKey": idempotency_key,
        "iat": now,
        "exp": now + 600,
    }
    token = jwt.encode(payload, secret_key(), algorithm="HS256")
    if isinstance(token, bytes):
        token = token.decode("utf-8")

    req = urllib.request.Request(
        f"{api_url()}/refunds",
        data=json.dumps({"data": token}).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except Exception as exc:
        detail = ""
        try:
            detail = exc.read().decode("utf-8")[:500]  # HTTPError body
        except Exception:
            pass
        raise MontonioError(
            f"Montonio refund create failed: {exc} {detail}".strip()) from exc
    if not isinstance(body, dict):
        raise MontonioError(f"Unexpected refund response: {body!r}")
    return body


def decode_order_token(token: str) -> dict:
    """Verify + decode Montonio orderToken (webhook or returnUrl ?order-token)."""
    return jwt.decode(token, secret_key(), algorithms=["HS256"])


def decode_refund_token(token: str) -> dict:
    """Verify + decode Montonio refundToken (refund webhooks)."""
    return jwt.decode(token, secret_key(), algorithms=["HS256"])
