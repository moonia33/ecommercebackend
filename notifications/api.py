"""Newsletter endpoints.

Public subscribe/unsubscribe (footer form) + authed preference toggles
for the account page. subscribe is idempotent and never leaks whether
an email already exists.
"""

from __future__ import annotations

from django.core.validators import validate_email
from django.core.exceptions import ValidationError
from django.utils import timezone

from ninja import Router, Schema
from ninja.errors import HttpError

from accounts.auth import JWTAuth

from .models import NewsletterSubscriber

router = Router(tags=["newsletter"])

_auth = JWTAuth()


class SubscribeIn(Schema):
    email: str
    source: str = "footer"


class StatusOut(Schema):
    subscribed: bool
    email: str = ""


class PreferenceIn(Schema):
    subscribed: bool


def _norm_email(raw: str) -> str:
    email = (raw or "").strip().lower()
    try:
        validate_email(email)
    except ValidationError:
        raise HttpError(400, "Invalid email")
    return email


def _subscribe(email: str, *, user=None, source: str = "") -> NewsletterSubscriber:
    sub, _ = NewsletterSubscriber.objects.get_or_create(
        email=email,
        defaults={"user": user, "source": source[:60]},
    )
    changed = False
    if user is not None and sub.user_id != user.id:
        sub.user = user
        changed = True
    if not sub.is_active:
        sub.is_active = True
        sub.unsubscribed_at = None
        changed = True
    if changed:
        sub.save()
    return sub


def _unsubscribe(email: str) -> None:
    sub = NewsletterSubscriber.objects.filter(email=email).first()
    if sub is not None and sub.is_active:
        sub.is_active = False
        sub.unsubscribed_at = timezone.now()
        sub.save(update_fields=["is_active", "unsubscribed_at", "updated_at"])


@router.post("/subscribe", response=StatusOut)
def subscribe(request, payload: SubscribeIn):
    email = _norm_email(payload.email)
    user = getattr(request, "auth", None) or _optional_user(request)
    sub = _subscribe(email, user=user, source=payload.source)
    return {"subscribed": True, "email": sub.email}


@router.post("/unsubscribe", response=StatusOut)
def unsubscribe(request, payload: SubscribeIn):
    email = _norm_email(payload.email)
    _unsubscribe(email)
    return {"subscribed": False, "email": email}


def _optional_user(request):
    try:
        cookie_name = "access_token"
        from django.conf import settings
        cookie_name = getattr(settings, "AUTH_COOKIE_ACCESS_NAME", cookie_name)
        token = (request.COOKIES.get(cookie_name) or "").strip()
        if not token:
            return None
        from accounts.jwt_utils import decode_token
        from django.contrib.auth import get_user_model
        payload = decode_token(token)
        if payload.get("type") != "access" or not payload.get("sub"):
            return None
        return get_user_model().objects.filter(
            id=int(payload["sub"]), is_active=True).first()
    except Exception:
        return None


@router.get("/status", response=StatusOut, auth=_auth)
def status(request):
    sub = NewsletterSubscriber.objects.filter(email=request.auth.email.lower()).first()
    return {"subscribed": bool(sub and sub.is_active), "email": request.auth.email}


@router.put("/preference", response=StatusOut, auth=_auth)
def preference(request, payload: PreferenceIn):
    email = request.auth.email.strip().lower()
    if payload.subscribed:
        _subscribe(email, user=request.auth, source="account")
        return {"subscribed": True, "email": email}
    _unsubscribe(email)
    return {"subscribed": False, "email": email}
