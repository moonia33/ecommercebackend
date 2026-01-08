from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import jwt
from django.conf import settings


@dataclass(frozen=True)
class NeopayConfigData:
    project_id: int
    project_key: str
    widget_host: str
    client_redirect_url: str


def get_neopay_config() -> NeopayConfigData | None:
    try:
        from payments.models import NeopayConfig

        cfg = NeopayConfig.objects.filter(is_active=True).order_by("-id").first()
        if not cfg:
            return None
        if not cfg.project_id or not (cfg.project_key or "").strip():
            return None
        return NeopayConfigData(
            project_id=int(cfg.project_id),
            project_key=str(cfg.project_key),
            widget_host=(cfg.widget_host or "https://psd2.neopay.lt/widget.html?").strip(),
            client_redirect_url=(cfg.client_redirect_url or "").strip(),
        )
    except Exception:
        return None


def build_neopay_payment_link(
    *,
    amount: Decimal,
    currency: str,
    transaction_id: str,
    payment_purpose: str,
) -> tuple[str, dict]:
    cfg = get_neopay_config()
    if not cfg:
        raise ValueError("Neopay config is not set")

    payload: dict = {
        "projectId": int(cfg.project_id),
        "amount": float(amount),
        "currency": currency,
        "transactionId": str(transaction_id),
        "paymentPurpose": str(payment_purpose)[:140],
        "serviceType": "pisp",
    }

    if cfg.client_redirect_url:
        payload["clientRedirectUrl"] = cfg.client_redirect_url

    now = datetime.now(tz=timezone.utc)
    payload["iat"] = int(now.timestamp())
    payload["exp"] = int((now + timedelta(minutes=30)).timestamp())

    token = jwt.encode(payload, cfg.project_key, algorithm="HS256")
    return f"{cfg.widget_host}{token}", payload


def decode_neopay_token(token: str) -> dict:
    cfg = get_neopay_config()
    if not cfg:
        raise ValueError("Neopay config is not set")

    return jwt.decode(token, cfg.project_key, algorithms=["HS256"])
