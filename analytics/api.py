from __future__ import annotations

from uuid import UUID

from ninja import Router, Schema

from api.shop_context import get_request_shop

from .models import CookieConsentRecord

router = Router(tags=["analytics"])


class CookieConsentIn(Schema):
    consent_id: UUID
    version: int
    action: str
    functional: bool = False
    analytics: bool = False
    marketing: bool = False


class OkOut(Schema):
    ok: bool = True


@router.post("/cookie-consent", response=OkOut)
def cookie_consent(request, payload: CookieConsentIn):
    action = payload.action
    if action not in CookieConsentRecord.Action.values:
        action = CookieConsentRecord.Action.CUSTOM
    CookieConsentRecord.objects.create(
        consent_id=payload.consent_id,
        shop=get_request_shop(request),
        version=max(payload.version, 0),
        action=action,
        functional=payload.functional,
        analytics=payload.analytics,
        marketing=payload.marketing,
        user_agent=(request.META.get("HTTP_USER_AGENT") or "")[:500],
    )
    return {"ok": True}
