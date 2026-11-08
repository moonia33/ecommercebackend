from __future__ import annotations

from uuid import UUID

from django.core.cache import cache
from ninja import Router, Schema
from ninja.errors import HttpError

from api.client_ip import client_ip

from api.shop_context import get_request_shop

from .models import CookieConsentRecord

router = Router(tags=["analytics"])

CONSENT_RATE_LIMIT = 30  # per IP per hour; a real visitor saves a handful at most


def _rate_limited(request) -> bool:
    ip = client_ip(request)
    if not ip:
        return False
    key = f"cookie-consent-rl:{ip}"
    cache.add(key, 0, 3600)
    try:
        return cache.incr(key) > CONSENT_RATE_LIMIT
    except ValueError:
        return False


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
    if _rate_limited(request):
        raise HttpError(429, "Too many requests")
    action = payload.action
    choice = {"functional": payload.functional, "analytics": payload.analytics,
              "marketing": payload.marketing}
    if action == CookieConsentRecord.Action.ACCEPT_ALL:
        choice = dict.fromkeys(choice, True)
    elif action == CookieConsentRecord.Action.REJECT_ALL:
        choice = dict.fromkeys(choice, False)
    else:
        action = CookieConsentRecord.Action.CUSTOM
    CookieConsentRecord.objects.create(
        consent_id=payload.consent_id,
        shop=get_request_shop(request),
        version=max(payload.version, 0),
        action=action,
        **choice,
        user_agent=(request.META.get("HTTP_USER_AGENT") or "")[:500],
    )
    return {"ok": True}
