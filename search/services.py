from __future__ import annotations

import logging
import re
import time
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
_search_down_until = 0.0
SEARCH_DOWN_SECONDS = 60.0


def search_available() -> bool:
    """search_enabled() + circuit breaker: po connection failure ~60s
    sync/užklausos mute'inamos — request'ai nekaba Typesense timeout'uose."""
    return search_enabled() and time.monotonic() >= _search_down_until


def note_search_failure(exc: BaseException) -> None:
    global _search_down_until
    now = time.monotonic()
    if now >= _search_down_until:
        logger.warning(
            "Typesense unreachable (%s); search muted for %ds",
            exc, int(SEARCH_DOWN_SECONDS),
        )
    _search_down_until = now + _SEARCH_DOWN_SECONDS


def _is_not_found(exc: BaseException) -> bool:
    from typesense.exceptions import ObjectNotFound

    return isinstance(exc, ObjectNotFound)

SCHEMA = {
    "name": COLLECTION_NAME,
    "fields": [
        {"name": "id", "type": "string"},
        {"name": "shop_ids", "type": "int32[]"},
        # NOTE: no `locale` on searchable fields — Typesense locale
        # processing runs before synonym lookup and breaks synonym matching
        # (verified on v28/v29). Diacritic folding is covered by the
        # pre-normalized `search_text` field instead.
        {"name": "name", "type": "string", "infix": True},
        {"name": "sku", "type": "string", "infix": True},
        {"name": "search_text", "type": "string", "optional": True, "infix": True},
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


def numeric_attribute_codes() -> list[str]:
    """Canonical codes of numeric attributes (Feature/OptionType with
    datatype 'number' or 'range'). These get `num_<code>` float[] fields
    in the Typesense schema for `range=` filtering."""
    from catalog.models import AttributeDatatype, Feature, OptionType

    numeric = (AttributeDatatype.NUMBER, AttributeDatatype.RANGE)
    codes = set(
        Feature.objects.filter(is_active=True, datatype__in=numeric)
        .values_list("code", flat=True)
    )
    codes |= set(
        OptionType.objects.filter(is_active=True, datatype__in=numeric)
        .values_list("code", flat=True)
    )
    return sorted(codes)


def num_field_name(code: str) -> str:
    # Typesense field names are restricted to [\w]+ — kebab '-' becomes '_'.
    return f"num_{code.replace('-', '_')}"


def get_schema() -> dict:
    """SCHEMA + dynamic float[] fields for every numeric attribute code."""
    fields = list(SCHEMA["fields"]) + [
        {
            "name": num_field_name(code),
            "type": "float[]",
            "facet": True,
            "optional": True,
        }
        for code in numeric_attribute_codes()
    ]
    return {**SCHEMA, "fields": fields}


def search_enabled() -> bool:
    return bool(getattr(settings, "TYPESENSE_API_KEY", ""))


def get_client():
    """Lazy Typesense client; None when unconfigured or circuit open."""
    global _client
    if _client is not None:
        return _client
    if not search_available():
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


def ensure_collection(*, force_recreate: bool = False) -> bool:
    client = get_client()
    if client is None:
        return False
    schema = get_schema()
    recreate = force_recreate
    if not recreate:
        try:
            existing = client.collections[COLLECTION_NAME].retrieve()

            def _sig(f: dict) -> tuple:
                return (
                    f["name"],
                    f["type"],
                    bool(f.get("facet")),
                    bool(f.get("optional")),
                    bool(f.get("infix")),
                    bool(f.get("index", True)),
                )

            # 'id' is implicit/managed by Typesense — excluded from the diff.
            wanted = {_sig(f) for f in schema["fields"] if f["name"] != "id"}
            actual = {_sig(f) for f in existing.get("fields", []) if f["name"] != "id"}
            missing = wanted - actual
            extra = actual - wanted
            if not extra and missing:
                # Purely additive drift (new num_* facet fields): add them
                # via the collection update endpoint — supported since
                # Typesense 0.24 — instead of dropping the whole index.
                defs_by_name = {f["name"]: f for f in schema["fields"]}
                try:
                    client.collections[COLLECTION_NAME].update(
                        {"fields": [defs_by_name[s[0]] for s in missing]}
                    )
                    return True
                except Exception:
                    logger.exception(
                        "Typesense field update failed; recreating collection"
                    )
                    recreate = True
            else:
                recreate = wanted != actual
        except Exception as exc:
            if not _is_not_found(exc):
                # Nepasiekiamas serveris — antras retrieve/create vis tiek
                # kabotų timeout'uose; mute'inam ir baigiam.
                note_search_failure(exc)
                return False
            recreate = True
    if recreate:
        try:
            client.collections[COLLECTION_NAME].delete()
        except Exception:
            pass
        try:
            client.collections.create(schema)
        except Exception as exc:
            if not _is_not_found(exc):
                note_search_failure(exc)
                return False
            raise
        return True
    try:
        client.collections[COLLECTION_NAME].retrieve()
    except Exception as exc:
        if not _is_not_found(exc):
            note_search_failure(exc)
            return False
        client.collections.create(schema)
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


def product_document(product, *, numeric_codes=None) -> dict | None:
    """Build the Typesense document for a product; None if not indexable."""
    from catalog.api.shared import _effective_offer_unit_net
    from catalog.models import InventoryItem

    p = product
    if not p.is_active:
        return None
    if numeric_codes is None:
        numeric_codes = set(numeric_attribute_codes())

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
    # Variant-level values are facets too (weight/size differ per variant);
    # a product is findable by any of its variants' feature values.
    feature_facets += [
        f"{vfv.feature.code}:{vfv.feature_value.value}"
        for v in variants
        for vfv in v.feature_values.all()
        if vfv.feature.is_active and vfv.feature_value.is_active
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

    # F4: numeric attribute values -> num_<code> float[] fields, enabling
    # `range=` filter_by clauses. ALL values are indexed (product-level +
    # variant-level features + variant option values). Range values
    # contribute both endpoints (lo, hi) — Typesense matches arrays
    # elementwise, so `num_x:[a..b]` is a "any endpoint inside" check; the
    # ORM range filter remains the authoritative overlap check.
    numeric: dict[str, list[float]] = {}

    def _push_num(code: str, value) -> None:
        if code not in numeric_codes or value.value_num is None:
            return
        arr = numeric.setdefault(num_field_name(code), [])
        lo = float(value.value_num)
        hi = float(value.value_num_max) if value.value_num_max is not None else lo
        lo, hi = min(lo, hi), max(lo, hi)
        if lo not in arr:
            arr.append(lo)
        if hi not in arr:
            arr.append(hi)

    for pfv in p.feature_values.select_related("feature", "feature_value"):
        if pfv.feature.is_active and pfv.feature_value.is_active:
            _push_num(pfv.feature.code, pfv.feature_value)
    for v in variants:
        for vfv in v.feature_values.all():
            if vfv.feature.is_active and vfv.feature_value.is_active:
                _push_num(vfv.feature.code, vfv.feature_value)
        for vov in v.option_values.all():
            if vov.option_type.is_active and vov.option_value.is_active:
                _push_num(vov.option_type.code, vov.option_value)

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
        **numeric,
    }


_DOC_QS_SELECT = ("brand", "category")
_DOC_QS_PREFETCH = ("variants", "variants__inventory_items",
                    "variants__feature_values", "variants__feature_values__feature",
                    "variants__feature_values__feature_value",
                    "variants__option_values", "variants__option_values__option_type",
                    "variants__option_values__option_value",
                    "feature_values", "feature_values__feature",
                    "feature_values__feature_value")


def index_products(product_ids: Iterable[int]) -> int:
    """Upsert documents for products; returns indexed count."""
    from catalog.models import Product

    client = get_client()
    if client is None or not ensure_collection():
        return 0

    ids = [int(pid) for pid in product_ids]
    qs = (
        Product.objects.filter(id__in=ids)
        .select_related(*_DOC_QS_SELECT)
        .prefetch_related(*_DOC_QS_PREFETCH)
    )
    docs = []
    seen_ids = set()
    stale_ids = []
    numeric_codes = set(numeric_attribute_codes())
    for p in qs:
        seen_ids.add(p.id)
        doc = product_document(p, numeric_codes=numeric_codes)
        if doc:
            docs.append(doc)
        else:
            stale_ids.append(p.id)
    # Unindexable (inactive) or deleted products must not linger in the index.
    stale_ids.extend(pid for pid in ids if pid not in seen_ids)
    collection = client.collections[COLLECTION_NAME]
    for pid in stale_ids:
        try:
            collection.documents[str(pid)].delete()
        except Exception as exc:
            if not _is_not_found(exc):
                note_search_failure(exc)
                break
    if not docs:
        return 0
    try:
        result = collection.documents.import_(
            docs, {"action": "upsert"})
    except Exception as exc:
        note_search_failure(exc)
        return 0
    if not isinstance(result, list):
        return 0
    failed = [r for r in result if not r.get("success")]
    if failed:
        import logging

        logging.getLogger(__name__).warning(
            "Typesense import: %d/%d docs rejected, first error: %s",
            len(failed), len(result), failed[0].get("error", ""),
        )
    return len(result) - len(failed)


def delete_product_document(product_id: int) -> None:
    client = get_client()
    if client is None:
        return
    try:
        client.collections[COLLECTION_NAME].documents[str(product_id)].delete()
    except Exception as exc:
        if not _is_not_found(exc):
            note_search_failure(exc)


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
    purged = _purge_stale_documents(client, {int(i) for i in ids})
    return {"indexed": indexed, "total_active": len(ids), "purged": purged}


def _purge_stale_documents(client, active_ids: set[int], *, batch_size: int = 200) -> int:
    """Delete collection docs whose ids are not active product ids.

    Upserts alone leave stale docs behind after DB reimports (old ids
    keep eating result slots); export + filter_by delete is the sweep.
    """
    import json as _json

    collection = client.collections[COLLECTION_NAME]
    try:
        exported = collection.documents.export({"include_fields": "id"})
    except Exception:
        logger.exception("Typesense export failed; skipping stale purge")
        return 0
    stale: list[str] = []
    for line in (exported or "").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            doc_id = str(_json.loads(line).get("id") or "")
        except ValueError:
            continue
        if not doc_id:
            continue
        try:
            if int(doc_id) in active_ids:
                continue
        except ValueError:
            pass  # non-product id in our collection: stale too
        stale.append(doc_id)
    purged = 0
    for i in range(0, len(stale), batch_size):
        batch = stale[i : i + batch_size]
        try:
            collection.documents.delete(
                {"filter_by": f"id:[{','.join(batch)}]"}
            )
            purged += len(batch)
        except Exception:
            logger.exception(
                "Typesense batch delete failed; falling back to per-doc"
            )
            for doc_id in batch:
                try:
                    collection.documents[doc_id].delete()
                    purged += 1
                except Exception:
                    pass
    return purged


def search_product_ids(
    *,
    q: str,
    shop_id: int | None,
    channel: str,
    limit: int = MAX_IDS,
    sort: str | None = None,
    ranges: list[tuple[str, object, object]] | None = None,
) -> list[int] | None:
    """Ordered product ids matching q. None when Typesense unavailable
    (caller should fall back to ORM search).

    `ranges` = parsed `range=` pairs [(code, min, max)] (Decimal|None
    bounds). Translated to `num_<code>:[min..max]` filter clauses —
    pre-filtering only; the ORM range filter applies on top.
    """
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

    if ranges:
        try:
            valid_fields = {num_field_name(c) for c in numeric_attribute_codes()}
        except Exception:
            valid_fields = None
        for code, lo, hi in ranges:
            field = num_field_name(code)
            # Unknown/non-numeric codes are skipped — the ORM filter is the
            # authoritative check; a clause on a field missing from the
            # schema would just error the whole search.
            if valid_fields is not None and field not in valid_fields:
                continue
            if lo is not None and hi is not None:
                filters.append(f"{field}:[{lo}..{hi}]")
            elif lo is not None:
                filters.append(f"{field}:>={lo}")
            elif hi is not None:
                filters.append(f"{field}:<={hi}")

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


def search_suggest(
    *,
    q: str,
    shop_id: int | None,
    channel: str,
    limit: int = 8,
) -> list[dict] | None:
    """Lengvi autocomplete pasiūlymai tiesiai iš Typesense — su <mark>
    highlight'ais. None = search nepasiekiamas (caller → ORM fallback).
    Grąžina dokumentų laukelius (be ORM enrichment — tai endpoint'o darbas).
    """
    client = get_client()
    if client is None:
        return None

    filters = [
        "vis_outlet:=true" if channel == "outlet" else "vis_normal:=true"
    ]
    if shop_id:
        filters.append(f"shop_ids:=[{int(shop_id)}]")

    params = {
        "q": q.strip(),
        "query_by": "name,sku,search_text",
        "query_by_weights": "5,3,1",
        "filter_by": " && ".join(filters),
        "per_page": min(20, max(1, limit)),
        "infix": "fallback",
        # 1 typo autocomplete'e — greičiau ir tikslesnė nei 2.
        "num_typos": 1,
        "prefix": "true,true,true",
        "highlight_fields": "name,sku",
        "highlight_start_tag": "<mark>",
        "highlight_end_tag": "</mark>",
        "include_fields": "id,slug,name,sku",
    }

    try:
        res = client.collections[COLLECTION_NAME].documents.search(params)
    except Exception as exc:
        note_search_failure(exc)
        return None

    out: list[dict] = []
    for h in res.get("hits", []):
        doc = h.get("document") or {}
        pid = doc.get("id")
        if pid is None:
            continue
        name_html = doc.get("name") or ""
        sku_html = ""
        for hl in h.get("highlights") or []:
            snippet = hl.get("snippet") or hl.get("value") or ""
            if hl.get("field") == "name" and snippet:
                name_html = snippet
            elif hl.get("field") == "sku" and snippet:
                sku_html = snippet
        out.append({
            "id": int(pid),
            "slug": doc.get("slug") or "",
            "name": doc.get("name") or "",
            "name_html": name_html,
            "sku": doc.get("sku") or "",
            "sku_html": sku_html,
        })
    return out


def sync_synonyms() -> dict:
    """Push active SearchSynonym rows to Typesense; remove stale managed ones."""
    from .models import SearchSynonym

    client = get_client()
    if client is None:
        return {"synced": 0, "error": "typesense not configured"}
    ensure_collection()

    wanted: dict[str, dict] = {}
    for s in SearchSynonym.objects.filter(is_active=True):
        # Typesense `root` synonyms map terms at *index time* only — they do
        # not expand queries. The admin `root` field means "canonical term":
        # we therefore push a multi-way synonym containing root + terms, so
        # a misspelled query (e.g. "vojdzer") finds "Voyager" products.
        terms = list(s.terms_list)
        if s.root and s.root.lower() not in {t.lower() for t in terms}:
            terms.append(s.root)
        wanted[s.typesense_id] = {"synonyms": terms}

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
