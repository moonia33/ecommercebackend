from __future__ import annotations

import ipaddress
from uuid import UUID

from django.core.cache import cache
from ninja import Router, Schema
from ninja.errors import HttpError

from api.client_ip import client_ip

from api.shop_context import get_request_shop

from .models import CookieConsentRecord

router = Router(tags=["analytics"])

CONSENT_RATE_LIMIT = 30  # per IP per hour; a real visitor saves a handful at most
REC_EVENTS_RATE_LIMIT = 240  # per IP per hour; batch'as iki 10 event'ų per POST
REC_EVENTS_MAX_PER_POST = 10
REC_EVENTS_MAX_ITEMS = 24


def _rate_limited(request) -> bool:
    ip = client_ip(request)
    # Private/loopback = the internal proxy hop (no CF header); don't pool all visitors.
    if not ip or ipaddress.ip_address(ip).is_private or ipaddress.ip_address(ip).is_loopback:
        return False
    key = f"cookie-consent-rl:{ip}"
    cache.add(key, 0, 3600)
    try:
        return cache.incr(key) > CONSENT_RATE_LIMIT
    except ValueError:
        return False


def _rec_events_rate_limited(request) -> bool:
    ip = client_ip(request)
    if not ip or ipaddress.ip_address(ip).is_private or ipaddress.ip_address(ip).is_loopback:
        return False
    key = f"rec-events-rl:{ip}"
    cache.add(key, 0, 3600)
    try:
        return cache.incr(key) > REC_EVENTS_RATE_LIMIT
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


class RecEventItemIn(Schema):
    product_id: int
    position: int = 0


class RecEventIn(Schema):
    # recommendation_impression | recommendation_click
    name: str
    kind: str = "cross_sell"
    placement: str = ""
    seed: str = ""
    product_id: int | None = None
    position: int = 0
    nonce: str = ""
    items: list[RecEventItemIn] = []


class RecEventsIn(Schema):
    events: list[RecEventIn]


class RecEventsOut(Schema):
    ok: bool = True
    accepted: int = 0


@router.post("/rec-events", response=RecEventsOut)
def rec_events(request, payload: RecEventsIn):
    """Recommendation impression/click beacons iš frontend'o.

    Impression = vienas event'as per sąrašą su `items` masyvu (ne per
    prekę — lentelės augimas kontroliuojamas). Click = per paspaudimą.
    """
    from .models import AnalyticsEvent
    from .services import track_event

    if _rec_events_rate_limited(request):
        raise HttpError(429, "Too many requests")

    allowed = {
        AnalyticsEvent.Name.RECOMMENDATION_IMPRESSION,
        AnalyticsEvent.Name.RECOMMENDATION_CLICK,
    }
    accepted = 0
    for ev in payload.events[:REC_EVENTS_MAX_PER_POST]:
        if ev.name not in allowed:
            continue
        items = [
            {"product_id": int(i.product_id), "position": int(i.position)}
            for i in ev.items[:REC_EVENTS_MAX_ITEMS]
        ]
        is_click = ev.name == AnalyticsEvent.Name.RECOMMENDATION_CLICK
        track_event(
            request=request,
            name=ev.name,
            object_type="product" if is_click and ev.product_id else "",
            object_id=ev.product_id if is_click else None,
            payload={
                "kind": ev.kind[:20],
                "placement": ev.placement[:32],
                "seed": ev.seed[:120],
                "position": int(ev.position),
                "nonce": ev.nonce[:64],
                **({"items": items} if items else {}),
            },
        )
        accepted += 1
    return {"ok": True, "accepted": accepted}


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
