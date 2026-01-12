from __future__ import annotations

import hashlib

from django.contrib.auth import get_user_model
from django.conf import settings
from django.db import transaction
from django.db import IntegrityError
from django.utils import timezone

from .models import AnalyticsEvent, AnalyticsOutbox, VisitorLink, RecentlyViewedProduct


User = get_user_model()


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _get_visitor_id_from_request(request) -> str:
    try:
        return (request.COOKIES.get("vid") or "").strip()
    except Exception:
        return ""


def _get_user_from_request(request):
    u = getattr(request, "user", None)
    if u is not None and getattr(u, "is_authenticated", False):
        return u

    # django-ninja auth backends (e.g. HttpBearer) typically set request.auth
    a = getattr(request, "auth", None)
    if isinstance(a, User):
        return a
    if a is not None and getattr(a, "is_authenticated", False):
        return a

    # Public endpoints may not run django-ninja auth. Try access token from HttpOnly cookie.
    try:
        from accounts.jwt_utils import decode_token

        cookie_name = getattr(settings, "AUTH_COOKIE_ACCESS_NAME", "access_token")
        token = (request.COOKIES.get(cookie_name) or "").strip()
        if not token:
            return None
        payload = decode_token(token)
        if payload.get("type") != "access":
            return None
        user_id = payload.get("sub")
        if not user_id:
            return None
        return User.objects.get(id=int(user_id), is_active=True)
    except Exception:
        return None

    return None


def _product_view_window_index(now) -> int:
    return int(int(now.timestamp()) // 1800)


def _recently_viewed_max() -> int:
    try:
        return max(1, int(getattr(settings, "RECENTLY_VIEWED_MAX", 12)))
    except Exception:
        return 12


def record_recently_viewed_product(*, request, product_id: int, now=None) -> None:
    now = now or timezone.now()

    visitor_id = _get_visitor_id_from_request(request)
    user = _get_user_from_request(request)
    if user is None and not visitor_id:
        return

    max_items = _recently_viewed_max()

    with transaction.atomic():
        if user is not None:
            RecentlyViewedProduct.objects.update_or_create(
                user=user,
                product_id=int(product_id),
                defaults={"visitor_id": "", "last_viewed_at": now},
            )
            ids_to_keep = list(
                RecentlyViewedProduct.objects.filter(user=user)
                .order_by("-last_viewed_at")
                .values_list("id", flat=True)[:max_items]
            )
            RecentlyViewedProduct.objects.filter(user=user).exclude(
                id__in=ids_to_keep
            ).delete()
        else:
            RecentlyViewedProduct.objects.update_or_create(
                user=None,
                visitor_id=str(visitor_id),
                product_id=int(product_id),
                defaults={"last_viewed_at": now},
            )
            ids_to_keep = list(
                RecentlyViewedProduct.objects.filter(user__isnull=True, visitor_id=str(visitor_id))
                .order_by("-last_viewed_at")
                .values_list("id", flat=True)[:max_items]
            )
            RecentlyViewedProduct.objects.filter(user__isnull=True, visitor_id=str(visitor_id)).exclude(
                id__in=ids_to_keep
            ).delete()


def merge_recently_viewed_from_visitor_to_user(*, request, user) -> None:
    visitor_id = _get_visitor_id_from_request(request)
    if not visitor_id or user is None:
        return

    max_items = _recently_viewed_max()
    now = timezone.now()

    with transaction.atomic():
        anon_qs = (
            RecentlyViewedProduct.objects.filter(user__isnull=True, visitor_id=str(visitor_id))
            .order_by("-last_viewed_at")
            .values_list("product_id", "last_viewed_at")
        )

        for product_id, last_viewed_at in anon_qs:
            RecentlyViewedProduct.objects.update_or_create(
                user=user,
                product_id=int(product_id),
                defaults={"visitor_id": "", "last_viewed_at": last_viewed_at or now},
            )

        # Remove anon list after merge
        RecentlyViewedProduct.objects.filter(user__isnull=True, visitor_id=str(visitor_id)).delete()

        # Enforce cap on user
        ids_to_keep = list(
            RecentlyViewedProduct.objects.filter(user=user)
            .order_by("-last_viewed_at")
            .values_list("id", flat=True)[:max_items]
        )
        RecentlyViewedProduct.objects.filter(user=user).exclude(id__in=ids_to_keep).delete()


def track_event(
    *,
    request,
    name: str,
    object_type: str = "",
    object_id: int | None = None,
    payload: dict | None = None,
    country_code: str = "",
    channel: str = "",
    language_code: str = "",
    outbox_providers: list[str] | None = None,
):
    now = timezone.now()
    payload = payload or {}

    visitor_id = _get_visitor_id_from_request(request)
    user = _get_user_from_request(request)

    raw_key = f"{name}:u:{getattr(user, 'id', 0)}:v:{visitor_id}:o:{object_type}:{object_id}:cc:{country_code}:ch:{channel}:lc:{language_code}"

    if name == AnalyticsEvent.Name.PRODUCT_VIEW:
        w = _product_view_window_index(now)
        raw_key = raw_key + f":w:{w}"
    elif name == AnalyticsEvent.Name.PURCHASE and object_type == "order" and object_id is not None:
        raw_key = f"purchase:u:{getattr(user, 'id', 0)}:order:{int(object_id)}"

    idempotency_key = _sha256(raw_key)

    try:
        ev = AnalyticsEvent.objects.create(
            name=name,
            occurred_at=now,
            user=user,
            visitor_id=visitor_id,
            object_type=(object_type or ""),
            object_id=object_id,
            country_code=(country_code or ""),
            channel=(channel or ""),
            language_code=(language_code or ""),
            payload=payload,
            idempotency_key=idempotency_key,
        )
    except IntegrityError:
        ev = None

    if name == AnalyticsEvent.Name.PRODUCT_VIEW and object_type == "product" and object_id is not None:
        try:
            record_recently_viewed_product(request=request, product_id=int(object_id), now=now)
        except Exception:
            pass

    if ev is None:
        return None

    if user is not None and visitor_id:
        try:
            VisitorLink.objects.update_or_create(
                user=user,
                visitor_id=visitor_id,
                defaults={},
            )
        except Exception:
            pass

    providers = outbox_providers or []
    for p in providers:
        try:
            AnalyticsOutbox.objects.get_or_create(event=ev, provider=str(p))
        except Exception:
            pass

    return ev
