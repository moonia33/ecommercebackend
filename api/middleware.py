from __future__ import annotations

from django.core.cache import cache

from .models import Shop, get_default_shop
from .shop_context import shop_context_strict


def _normalize_host(host: str) -> str:
    h = (host or "").strip().lower()
    if ":" in h:
        h = h.split(":", 1)[0]
    return h


class ShopResolverMiddleware:
    """
    Resolves request.shop and request.shop_id from Host header.
    Falls back to the default active shop.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        shop = self._resolve_shop(request)
        request.shop = shop
        request.shop_id = int(shop.id) if shop else None
        return self.get_response(request)

    def _resolve_shop(self, request) -> Shop | None:
        host = _normalize_host(getattr(request, "get_host", lambda: "")())
        cache_key = f"shop-by-host:{host or 'default'}"

        cached_id = cache.get(cache_key)
        if cached_id:
            s = Shop.objects.filter(id=int(cached_id), is_active=True).first()
            if s is not None:
                return s

        if host:
            s = Shop.objects.filter(is_active=True, primary_domain=host).first()
            if s is None:
                # Exact host match in additional_domains CSV list.
                for candidate in Shop.objects.filter(is_active=True).only("id", "code", "primary_domain", "additional_domains"):
                    for domain in candidate.all_domains[1:]:
                        if domain == host:
                            s = candidate
                            break
                    if s is not None:
                        break
            if s is not None:
                cache.set(cache_key, int(s.id), timeout=300)
                return s

        if shop_context_strict():
            return None

        s = get_default_shop()
        if s is not None:
            cache.set(cache_key, int(s.id), timeout=120)
        return s
