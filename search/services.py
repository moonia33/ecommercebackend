from __future__ import annotations

import logging
import re
import unicodedata
from typing import Iterable

from django.conf import settings
from django.utils import timezone

logger = logging.getLogger(__name__)

COLLECTION_NAME = "products"
SEARCH_TIMEOUT_SECONDS = 2
MAX_IDS = 500  # two 250-hit Typesense pages; browse lists are smaller than this

SORT_MAP = {
    "price": "price_normal:asc",
    "-price": "price_normal:desc",
    "-created": "created_ts:desc",
    "-created_at": "created_ts:desc",
    "created": "created_ts:asc",
    "created_at": "created_ts:asc",
}

_client = None
_client_failed = False

SCHEMA = {
    "name": COLLECTION_NAME,
    "fields": [
        {"name": "id", "type": "string"},
        {"name": "shop_ids", "type": "int32[]"},
        {"name": "name", "type": "string", "locale": "lt"},
        {"name": "sku", "type": "string"},
        {"name": "search_text", "type": "string", "locale": "lt", "optional": True},
        {"name": "slug", "type": "string", "index": False},
        {"name": "category_ids", "type": "int32[]", "facet": True},
        {"name": "brand_id", "type": "int32", "facet": True, "optional": True},
        {"name": "group_id", "type": "int32", "facet": True, "optional": True},
        {"name": "feature_facets", "type": "string[]", "facet": True},
        {"name": "price_normal", "type": "float", "facet": True, "optional": True},
        {"name": "price_outlet", "type": "float", "facet": True, "optional": True},
        {"name": "vis_normal", "type": "bool"},
        {"name": "vis_outlet", "type": "bool"},
        {"name": "created_ts", "type": "int64"},
        {"name": "popularity", "type": "int32"},
    ],
    "default_sorting_field": "popularity",
}


def search_enabled() -> bool:
    return bool(getattr(settings, "TYPESENSE_API_KEY", ""))


def get_client():
    """Lazy Typesense client; None when unconfigured."""
    global _client
    if _client is not None:
        return _client
    if not search_enabled():
        return None
    import typesense

    _client = typesense.Client({
        "nodes": [{
            "host": getattr(settings, "TYPESENSE_HOST", "localhost"),
            "port": getattr(settings, "TYPESENSE_PORT", "8108"),
            "protocol": getattr(settings, "TYPESENSE_PROTOCOL", "http"),
        }],
        "api_key": settings.TYPESENSE_API_KEY,
        "connection_timeout_seconds": SEARCH_TIMEOUT_SECONDS,
    })
    return _client


def ensure_collection() -> bool:
    client = get_client()
    if client is None:
        return False
    try:
        client.collections[COLLECTION_NAME].retrieve()
    except Exception:
        client.collections.create(SCHEMA)
    return True


def _strip_tags(text: str) -> str:
    text = re.sub(r"<[^>]+>", " ", text or "")
    text = re.sub(r"[#*_`>\[\]()]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _norm(text: str) -> str:
    """Diacritic-folded + split punctuation for the LT fallback field."""
    text = unicodedata.normalize("NFKD", text or "")
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = re.sub(r"[^0-9A-Za-zÀ-ž ]+", " ", text)
    return re.sub(r"\s+", " ", text).strip().lower()


def _shop_ids_for_product(product_id: int) -> list[int]:
    from api.models import Shop
    from catalog.models import Product
    from catalog.shop_scope import apply_shop_product_scope

    ids: list[int] = []
    for shop in Shop.objects.filter(is_active=True).only("id"):
        if apply_shop_product_scope(
            qs=Product.objects.filter(id=product_id), shop_id=shop.id
        ).exists():
            ids.append(int(shop.id))
    return ids


def product_document(product) -> dict | None:
    """Build the Typesense document for a product; None if not indexable."""
    from catalog.api.shared import _effective_offer_unit_net
    from catalog.models import InventoryItem

    p = product
    if not p.is_active:
        return None

    variants = [v for v in p.variants.all() if v.is_active]
    offer_items = [
        ii for v in variants for ii in v.inventory_items.all()
        if ii.qty_on_hand > ii.qty_reserved
    ]

    def _vis_price(visibility: str):
        prices = [
            _effective_offer_unit_net(
                list_unit_net=ii.variant.price_eur, offer=ii)
            for ii in offer_items
            if ii.offer_visibility == visibility
        ]
        prices = [pr for pr in prices if pr is not None]
        return min(prices) if prices else None

    price_normal = _vis_price(InventoryItem.OfferVisibility.NORMAL)
    price_outlet = _vis_price(InventoryItem.OfferVisibility.OUTLET)

    feature_facets = [
        f"{pfv.feature.code}:{pfv.feature_value.value}"
        for pfv in p.feature_values.select_related("feature", "feature_value")
        if pfv.feature.is_active and pfv.feature_value.is_active
    ]

    search_bits = [
        p.name, p.sku,
        *(v.sku for v in variants),
        p.brand.name if p.brand else "",
        p.category.name if p.category else "",
        *(c.name for c in (p.category.get_ancestors() if p.category else [])),
        *(f.split(":", 1)[1] for f in feature_facets),
        _strip_tags(getattr(p, "description", ""))[:2000],
    ]
    search_text = " ".join(b for b in search_bits if b)
    norm_text = _norm(search_text)
    if norm_text and norm_text not in search_text.lower():
        search_text = f"{search_text} {norm_text}"

    category_ids = []
    if p.category_id:
        category_ids = [c.id for c in p.category.get_ancestors()] + [p.category_id]

    return {
        "id": str(p.id),
        "shop_ids": _shop_ids_for_product(p.id),
        "name": p.name or "",
        "sku": p.sku or "",
        "search_text": search_text[:6000],
        "slug": p.slug or "",
        "category_ids": category_ids,
        "brand_id": int(p.brand_id) if p.brand_id else None,
        "group_id": int(p.group_id) if p.group_id else None,
        "feature_facets": feature_facets,
        "price_normal": float(price_normal) if price_normal is not None else None,
        "price_outlet": float(price_outlet) if price_outlet is not None else None,
        "vis_normal": price_normal is not None,
        "vis_outlet": price_outlet is not None,
        "created_ts": int(p.created_at.timestamp()) if p.created_at else 0,
        "popularity": 0,
    }


_DOC_QS_SELECT = ("brand", "category")
_DOC_QS_PREFETCH = ("variants", "variants__inventory_items",
                    "feature_values", "feature_values__feature",
                    "feature_values__feature_value")


def index_products(product_ids: Iterable[int]) -> int:
    """Upsert documents for products; returns indexed count."""
    from catalog.models import Product

    client = get_client()
    if client is None or not ensure_collection():
        return 0

    qs = (
        Product.objects.filter(id__in=list(product_ids))
        .select_related(*_DOC_QS_SELECT)
        .prefetch_related(*_DOC_QS_PREFETCH)
    )
    docs = [d for d in (product_document(p) for p in qs) if d]
    if not docs:
        return 0
    result = client.collections[COLLECTION_NAME].documents.import_(
        docs, {"action": "upsert"})
    return len(docs) if isinstance(result, list) else 0


def delete_product_document(product_id: int) -> None:
    client = get_client()
    if client is None:
        return
    try:
        client.collections[COLLECTION_NAME].documents[str(product_id)].delete()
    except Exception:
        pass


def full_reindex() -> dict:
    from catalog.models import Product

    client = get_client()
    if client is None:
        return {"indexed": 0, "error": "typesense not configured"}
    ensure_collection()
    ids = list(
        Product.objects.filter(is_active=True).values_list("id", flat=True)
    )
    indexed = index_products(ids)
    return {"indexed": indexed, "total_active": len(ids)}


def search_product_ids(
    *,
    q: str,
    shop_id: int | None,
    channel: str,
    limit: int = MAX_IDS,
    sort: str | None = None,
) -> list[int] | None:
    """Ordered product ids matching q. None when Typesense unavailable
    (caller should fall back to ORM search)."""
    global _client_failed
    client = get_client()
    if client is None:
        return None

    filters = []
    if shop_id:
        filters.append(f"shop_ids:=[{int(shop_id)}]")
    filters.append(
        "vis_outlet:=true" if channel == "outlet" else "vis_normal:=true"
    )

    sort_by = SORT_MAP.get((sort or "").strip().lower(), "")

    params = {
        "q": q.strip(),
        "query_by": "name,sku,search_text",
        "query_by_weights": "4,3,1",
        "filter_by": " && ".join(filters),
        "per_page": min(250, max(1, limit)),
        "infix": "fallback",
        "num_typos": 2,
    }
    if sort_by:
        params["sort_by"] = sort_by

    try:
        res = client.collections[COLLECTION_NAME].documents.search(params)
        hits = res.get("hits", [])
        ids = [int(h["document"]["id"]) for h in hits]

        if len(hits) >= params["per_page"] and len(ids) < limit:
            params["page"] = 2
            res2 = client.collections[COLLECTION_NAME].documents.search(params)
            ids += [int(h["document"]["id"]) for h in res2.get("hits", [])]
        return ids[:limit]
    except Exception:
        if not _client_failed:
            logger.exception("Typesense search failed; falling back to ORM")
            _client_failed = True
        return None


def sync_synonyms() -> dict:
    """Push active SearchSynonym rows to Typesense; remove stale managed ones."""
    from .models import SearchSynonym

    client = get_client()
    if client is None:
        return {"synced": 0, "error": "typesense not configured"}
    ensure_collection()

    wanted: dict[str, dict] = {}
    for s in SearchSynonym.objects.filter(is_active=True):
        body = {"synonyms": s.terms_list}
        if s.root:
            body["root"] = s.root
        wanted[s.typesense_id] = body

    collection = client.collections[COLLECTION_NAME]
    try:
        existing = collection.synonyms.retrieve().get("synonyms", [])
        for item in existing:
            sid = item.get("id", "")
            if sid.startswith("syn-") and sid not in wanted:
                collection.synonyms[sid].delete()
        for sid, body in wanted.items():
            collection.synonyms.upsert(sid, body)
    except Exception:
        logger.exception("Failed to sync synonyms")
        return {"synced": 0, "error": "typesense sync failed"}
    return {"synced": len(wanted)}


def sync_overrides() -> dict:
    """Push currently-valid SearchOverride rules to Typesense."""
    from .models import SearchOverride

    client = get_client()
    if client is None:
        return {"synced": 0, "error": "typesense not configured"}
    ensure_collection()

    now = timezone.now()
    wanted: dict[str, dict] = {}
    for o in SearchOverride.objects.prefetch_related("pins", "excludes"):
        if not o.is_currently_valid(now=now):
            continue
        includes = [
            {"id": str(pin.product_id), "position": pin.position}
            for pin in o.pins.all()
        ]
        excludes = [
            {"id": str(ex.product_id)} for ex in o.excludes.all()
        ]
        if not includes and not excludes:
            continue
        wanted[o.typesense_id] = {
            "rule": {"q": o.query, "match": o.match},
            "includes": includes,
            "excludes": excludes,
        }

    collection = client.collections[COLLECTION_NAME]
    try:
        existing = collection.overrides.retrieve().get("overrides", [])
        for item in existing:
            oid = item.get("id", "")
            if oid.startswith("ovr-") and oid not in wanted:
                collection.overrides[oid].delete()
        for oid, body in wanted.items():
            collection.overrides.upsert(oid, body)
    except Exception:
        logger.exception("Failed to sync overrides")
        return {"synced": 0, "error": "typesense sync failed"}
    return {"synced": len(wanted)}


def sync_curation() -> dict:
    return {**sync_synonyms(), "overrides": sync_overrides()}
