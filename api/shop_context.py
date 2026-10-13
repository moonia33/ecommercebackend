from __future__ import annotations

from django.conf import settings

from .models import Shop, get_default_shop


def shop_context_enabled() -> bool:
    return bool(getattr(settings, "SHOP_CONTEXT_ENABLED", True))


def shop_context_strict() -> bool:
    return bool(getattr(settings, "SHOP_CONTEXT_STRICT", False))


def get_request_shop(request) -> Shop | None:
    if not shop_context_enabled():
        return None
    s = getattr(request, "shop", None)
    if isinstance(s, Shop):
        return s
    if shop_context_strict():
        return None
    return get_default_shop()
