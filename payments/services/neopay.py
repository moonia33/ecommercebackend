from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import jwt


@dataclass(frozen=True)
class NeopayConfigData:
    project_id: int
    project_key: str
    widget_host: str
    client_redirect_url: str
    enable_bank_preselect: bool
    banks_api_base_url: str


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
            project_key=str(cfg.project_key).strip(),
            widget_host=(cfg.widget_host or "https://psd2.neopay.lt/widget.html?").strip(),
            client_redirect_url=(cfg.client_redirect_url or "").strip(),
            enable_bank_preselect=bool(getattr(cfg, "enable_bank_preselect", False)),
            banks_api_base_url=(getattr(cfg, "banks_api_base_url", "") or "https://psd2.neopay.lt/api").strip(),
        )
    except Exception:
        return None


def build_neopay_payment_link(
    *,
    amount: Decimal,
    currency: str,
    transaction_id: str,
    payment_purpose: str,
    bank_bic: str | None = None,
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

    bank_bic = (bank_bic or "").strip()
    if bank_bic and cfg.enable_bank_preselect:
        payload["bank"] = bank_bic

    if cfg.client_redirect_url:
        payload["clientRedirectUrl"] = cfg.client_redirect_url

    now = datetime.now(tz=timezone.utc)
    payload["iat"] = int(now.timestamp())
    payload["exp"] = int((now + timedelta(minutes=30)).timestamp())

    token = jwt.encode(payload, cfg.project_key, algorithm="HS256")

    widget_host = (cfg.widget_host or "").strip()
    if not widget_host:
        widget_host = "https://psd2.neopay.lt/widget.html?"
    if widget_host.endswith("?") or widget_host.endswith("&"):
        base = widget_host
    elif "?" in widget_host:
        base = f"{widget_host}&"
    else:
        base = f"{widget_host}?"

    return f"{base}{token}", payload


def decode_neopay_token(token: str) -> dict:
    cfg = get_neopay_config()
    if not cfg:
        raise ValueError("Neopay config is not set")

    return jwt.decode(token, cfg.project_key, algorithms=["HS256"])
