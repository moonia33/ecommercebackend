"""Kategorijų hero/kortelių nuotraukų rendition'os (AVIF/WebP, keli pločiai).

Šaltinis – įkeltas failas (hero_image/card_image) arba išorinis URL
(hero_image_url/card_image_url). Rendition'ų vardai turi turinio hash'ą, todėl
CDN juos gali kešuoti kaip immutable. `Category.image_renditions`:
  {"hero": {"src": "<šaltinis>", "width": W, "height": H,
            "avif": [[name, w], ...], "webp": [[name, w], ...]}, "card": {...}}
"""

from __future__ import annotations

import hashlib
import logging
from io import BytesIO

from django.conf import settings
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage

logger = logging.getLogger(__name__)

KINDS = {
    "hero": ("hero_image", "hero_image_url", (640, 960, 1280, 1600)),
    "card": ("card_image", "card_image_url", (320, 480, 720, 960)),
}


def rendition_source(category, kind: str) -> str:
    file_field, url_field, _ = KINDS[kind]
    f = getattr(category, file_field)
    if f:
        return f"file:{f.name}"
    url = (getattr(category, url_field) or "").strip()
    return f"url:{url}" if url else ""


def needs_renditions(category, kind: str) -> bool:
    src = rendition_source(category, kind)
    current = (category.image_renditions or {}).get(kind) or {}
    return (current.get("src") or "") != src


def _check_public_url(url: str) -> None:
    """SSRF apsauga: tik http(s) į viešus adresus."""
    import ipaddress
    import socket
    from urllib.parse import urlsplit

    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise ValueError("unsupported url")
    for info in socket.getaddrinfo(parts.hostname, parts.port or None, proto=socket.IPPROTO_TCP):
        ip = ipaddress.ip_address(info[4][0])
        if not ip.is_global:
            raise ValueError(f"non-public host {parts.hostname}")


def _download(url: str) -> bytes:
    import urllib.request

    class _CheckedRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            _check_public_url(newurl)
            return super().redirect_request(req, fp, code, msg, headers, newurl)

    max_bytes = int(getattr(settings, "IMAGE_INGEST_MAX_BYTES", 8 * 1024 * 1024))
    timeout = int(getattr(settings, "IMAGE_INGEST_TIMEOUT", 20))
    _check_public_url(url)
    opener = urllib.request.build_opener(_CheckedRedirect)
    req = urllib.request.Request(url, headers={"User-Agent": "catalog-image-ingest/1.0"})
    with opener.open(req, timeout=timeout) as resp:
        ctype = (resp.headers.get("Content-Type") or "").split(";")[0].strip().lower()
        if ctype and not ctype.startswith("image/"):
            raise ValueError(f"unsupported content-type '{ctype}'")
        data = resp.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise ValueError("file too large")
    return data


def _read_source(category, kind: str) -> bytes:
    file_field, url_field, _ = KINDS[kind]
    f = getattr(category, file_field)
    if f:
        f.open("rb")
        try:
            return f.read()
        finally:
            f.close()
    return _download(getattr(category, url_field).strip())


def build_renditions(category, kind: str) -> dict:
    """Sugeneruoja ir įrašo į storage `kind` rendition'as; grąžina JSON įrašą."""
    from PIL import Image
    import pillow_avif  # noqa: F401

    src = rendition_source(category, kind)
    if not src:
        return {}
    data = _read_source(category, kind)
    digest = hashlib.sha1(data).hexdigest()[:10]
    img = Image.open(BytesIO(data))
    img.load()
    if img.mode != "RGB":
        img = img.convert("RGB")
    width, height = img.size
    widths = KINDS[kind][2]
    targets = [w for w in widths if w < width]
    if width <= widths[-1] or not targets:
        targets.append(min(width, widths[-1]))
    out = {"src": src, "width": width, "height": height, "avif": [], "webp": []}
    for w in sorted(set(targets)):
        r = img if w >= width else img.resize((w, max(1, round(height * w / width))), Image.LANCZOS)
        for fmt, ext, kw in (
            ("AVIF", "avif", {"quality": 60}),
            ("WEBP", "webp", {"quality": 78, "method": 6}),
        ):
            buf = BytesIO()
            r.save(buf, format=fmt, **kw)
            name = default_storage.save(
                f"category-renditions/{category.pk}/{kind}_{digest}_{w}.{ext}",
                ContentFile(buf.getvalue()),
            )
            out[ext].append([name, w])
    return out


def _delete_files(entry: dict, keep: set[str]) -> None:
    for ext in ("avif", "webp"):
        for name, _w in entry.get(ext) or []:
            if name in keep:
                continue
            try:
                default_storage.delete(name)
            except Exception:
                logger.warning("category rendition delete failed: %s", name)


def process_category(category, *, force: bool = False) -> list[str]:
    """Atnaujina pasikeitusias rendition'as. Grąžina atnaujintus `kind`'us."""
    from django.db import transaction

    built: dict[str, dict] = {}
    for kind in KINDS:
        if not force and not needs_renditions(category, kind):
            continue
        try:
            built[kind] = build_renditions(category, kind)
        except Exception as exc:
            logger.warning("category %s %s renditions failed: %s", category.pk, kind, exc)
            # Šaltinis pažymimas, kad nebandytume kas kartą; API grąžins originalą.
            built[kind] = {"src": rendition_source(category, kind), "error": str(exc)[:200]}
    if not built:
        return []

    changed: list[str] = []
    stale: list[dict] = []
    with transaction.atomic():
        # Lygiagretus task'as galėjo apdoroti naujesnį šaltinį – rašom tik
        # tas rendition'as, kurių šaltinis vis dar sutampa su DB.
        current = type(category).objects.select_for_update().get(pk=category.pk)
        renditions = dict(current.image_renditions or {})
        for kind, new in built.items():
            if (new.get("src") or "") != rendition_source(current, kind):
                stale.append(new)
                continue
            old = renditions.get(kind) or {}
            if old.get("src") and old is not new:
                stale.append(old)
            if new.get("src"):
                renditions[kind] = new
            else:
                renditions.pop(kind, None)
            changed.append(kind)
        if changed:
            current.image_renditions = renditions
            current.save(update_fields=["image_renditions"])
    category.image_renditions = current.image_renditions
    keep = {n for e in renditions.values() for ext in ("avif", "webp") for n, _w in e.get(ext) or []}
    for entry in stale:
        _delete_files(entry, keep)
    return changed


def category_image_out(category, kind: str) -> dict | None:
    """API: {url, width, height, avif_srcset, webp_srcset} arba None.

    `url` – didžiausia WebP rendition'a (originalas, kol jų dar nėra)."""
    fallback_url = category.hero_url if kind == "hero" else category.card_url
    if not fallback_url:
        return None
    entry = (category.image_renditions or {}).get(kind) or {}
    if entry.get("src") != rendition_source(category, kind) or not entry.get("webp"):
        return {"url": fallback_url, "width": 0, "height": 0, "avif_srcset": [], "webp_srcset": []}

    def _set(ext):
        return [{"url": default_storage.url(n), "width": int(w)} for n, w in entry.get(ext) or []]

    webp = _set("webp")
    return {
        "url": webp[-1]["url"],
        "width": int(entry.get("width") or 0),
        "height": int(entry.get("height") or 0),
        "avif_srcset": _set("avif"),
        "webp_srcset": webp,
    }


def category_images(category) -> dict:
    """API laukai: hero/card objektai + suderinami *_url (optimizuotas WebP)."""
    hero = category_image_out(category, "hero")
    card = category_image_out(category, "card")
    return {
        "hero_image_url": hero["url"] if hero else None,
        "card_image_url": card["url"] if card else None,
        "hero_image": hero,
        "card_image": card,
    }
