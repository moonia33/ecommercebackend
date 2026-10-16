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


def build_order_payload(*, order, language_code: str | None = None) -> dict:
    """Montonio Stargate order payload (JWT claims)."""
    grand_total = Decimal(order.total_gross)
    locale = (language_code or getattr(settings, "MONTONIO_LOCALE", "lt") or "lt").lower()[:2]

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

    merchant_reference = str(order.id)

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
            "method": "paymentInitiation",
            "methodDisplay": "Pay with your bank",
            "methodOptions": {
                "paymentDescription": f"Order {merchant_reference}",
                "preferredCountry": (order.country_code or "LT"),
                "preferredLocale": locale,
            },
            "amount": float(grand_total),
            "currency": order.currency or "EUR",
        },
    }


def create_order_payment(*, order, language_code: str | None = None,
                         timeout: int = 20) -> MontonioOrderResult:
    """Create a Montonio Stargate order and return the redirect URL."""
    if not is_configured():
        raise MontonioError("Montonio is not configured (missing access/secret key)")

    payload = build_order_payload(order=order, language_code=language_code)
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


def decode_order_token(token: str) -> dict:
    """Verify + decode Montonio orderToken (webhook or returnUrl ?order-token)."""
    return jwt.decode(token, secret_key(), algorithms=["HS256"])


def decode_refund_token(token: str) -> dict:
    """Verify + decode Montonio refundToken (refund webhooks)."""
    return jwt.decode(token, secret_key(), algorithms=["HS256"])
