from __future__ import annotations

from django.core.cache import cache

from .models import Site


class SiteMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request.site = self._resolve_site(request)
        return self.get_response(request)

    def _resolve_site(self, request):
        host = ""
        try:
            host = (request.get_host() or "").split(":", 1)[0].strip().lower()
        except Exception:
            host = ""

        cache_key = f"site_id_by_host:v1:{host}" if host else "site_id_by_host:v1:"
        cached_id = cache.get(cache_key)
        if isinstance(cached_id, int) and cached_id > 0:
            s = Site.objects.filter(id=int(cached_id), is_active=True).first()
            if s is not None:
                return s

        site = None
        if host:
            site = Site.objects.filter(is_active=True, primary_domain__iexact=host).first()

        if site is None:
            site = Site.objects.filter(is_active=True, code="default").first()

        if site is None:
            site = Site.objects.filter(is_active=True).order_by("code").first()

        cache.set(cache_key, int(site.id) if site is not None else 0, timeout=60)
        return site
