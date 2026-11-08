from __future__ import annotations

import ipaddress

from django.conf import settings


def client_ip(request) -> str | None:
    """Real visitor IP.

    Production chain: Cloudflare -> Traefik -> SvelteKit -> Django; the origin only
    accepts Cloudflare IPs, and Cloudflare always overwrites ``CF-Connecting-IP``.
    ``X-Forwarded-For`` is not trusted: its first hop is whatever the client sent.
    """
    header = getattr(settings, "CLIENT_IP_HEADER", "HTTP_CF_CONNECTING_IP")
    for raw in ((request.META.get(header) if header else None), request.META.get("REMOTE_ADDR")):
        value = (raw or "").strip()
        if not value:
            continue
        try:
            return str(ipaddress.ip_address(value))
        except ValueError:
            continue
    return None
