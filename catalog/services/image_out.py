"""ProductImage -> API schema serializeriai su srcset rinkiniais.

Du kontekstai:
  - "listing": kvadratines rendition'os (sq300/sq600) kortelems;
  - "detail":  proporcingos (m300/d800) PDP galerijai, `full_url` = originalas
               zoom'ui.
"""

from __future__ import annotations

from catalog.models import ProductImage


def _src(*, url: str, width: int) -> dict:
    return {"url": url, "width": width}


def image_out(img: ProductImage, *, context: str = "listing") -> dict | None:
    """Vienas ProductImage -> ProductImageOut dict. None jei nėra ko rodyti."""
    base = img.url
    if not base:
        return None

    if context == "detail":
        avif_primary = img.detail_avif_url or img.avif_url or None
        webp_primary = img.detail_webp_url or img.webp_url or None
        avif_srcset = [
            _src(url=u, width=w)
            for u, w in (
                (img.avif_url, 300),
                (img.detail_avif_url, 800),
            )
            if u
        ]
        webp_srcset = [
            _src(url=u, width=w)
            for u, w in (
                (img.webp_url, 300),
                (img.detail_webp_url, 800),
            )
            if u
        ]
    else:
        avif_primary = img.listing_avif_url or img.avif_url or None
        webp_primary = img.listing_webp_url or img.webp_url or None
        avif_srcset = [
            _src(url=u, width=w)
            for u, w in (
                (img.listing_avif_url, 300),
                (img.listing_avif_2x_url, 600),
            )
            if u
        ]
        webp_srcset = [
            _src(url=u, width=w)
            for u, w in (
                (img.listing_webp_url, 300),
                (img.listing_webp_2x_url, 600),
            )
            if u
        ]

    fallback = avif_primary or webp_primary or base
    return {
        "avif_url": avif_primary,
        "webp_url": webp_primary,
        "url": fallback,
        "alt_text": img.alt_text,
        "sort_order": img.sort_order,
        "avif_srcset": avif_srcset,
        "webp_srcset": webp_srcset,
        "full_url": img.original_url or None,
    }
