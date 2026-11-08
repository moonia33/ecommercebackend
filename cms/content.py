"""Shared storefront content helpers for CMS pages and blog posts."""
from __future__ import annotations

import logging

from .richtext import doc_to_segments

logger = logging.getLogger(__name__)


def products_payload(ids: list[int], *, shop, language_code: str) -> list[dict]:
    if not ids:
        return []
    from catalog.services import serialize_products_listing
    from recommendations.services import ordered_product_qs

    shop_id = int(shop.id) if shop is not None else None
    try:
        qs = ordered_product_qs(ids=ids, shop_id=shop_id, channel="normal")
        items = serialize_products_listing(
            qs=qs, country_code="LT", channel="normal", shop_id=shop_id, language_code=language_code,
        )
    except Exception:
        # A product card must never take the article down with it.
        logger.exception("CMS product block failed for ids=%s", ids)
        return []
    return [i.model_dump(mode="json") if hasattr(i, "model_dump") else dict(i) for i in items]


def rich_content_blocks(doc, *, shop, language_code: str) -> list[dict]:
    """Tiptap JSON -> ``[{"type": "html"|"products", "payload": {...}}]``."""
    blocks: list[dict] = []
    for seg in doc_to_segments(doc):
        if seg["type"] == "html":
            blocks.append({"type": "html", "payload": {"html": seg["html"]}})
            continue
        products = products_payload(seg["product_ids"], shop=shop, language_code=language_code)
        if products:
            blocks.append({"type": "products", "payload": {"title": seg["title"], "products": products}})
    return blocks
