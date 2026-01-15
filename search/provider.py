from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .meili import MeiliClient, MeiliError


@dataclass(frozen=True)
class SearchResult:
    ids: list[int]
    estimated_total: int | None


@dataclass(frozen=True)
class FacetResult:
    distribution: dict


def _escape_filter_value_int_list(values: list[int]) -> str:
    vs = ",".join(str(int(v)) for v in values)
    return f"[{vs}]"


def build_meili_filter(
    *,
    site_id: int | None,
    channel: str,
    in_stock_only: bool,
    category_ancestor_ids: list[int] | None,
    brand_id: int | None,
    group_id: int | None,
    feature_value_ids: list[int] | None,
    option_value_ids: list[int] | None,
) -> str:
    parts: list[str] = []

    if site_id is not None:
        parts.append(f"visible_in_site_ids = {int(site_id)}")

    if category_ancestor_ids:
        ids = [int(i) for i in category_ancestor_ids]
        if len(ids) == 1:
            parts.append(f"category_ancestor_ids = {int(ids[0])}")
        else:
            parts.append(f"category_ancestor_ids IN {_escape_filter_value_int_list(ids)}")

    if brand_id is not None:
        parts.append(f"brand_id = {int(brand_id)}")

    if group_id is not None:
        parts.append(f"group_id = {int(group_id)}")

    if in_stock_only:
        if channel == "outlet":
            parts.append("has_stock_outlet = true")
        else:
            parts.append("has_stock_normal = true")

    if feature_value_ids:
        ids = [int(i) for i in feature_value_ids]
        if len(ids) == 1:
            parts.append(f"feature_value_ids = {int(ids[0])}")
        else:
            parts.append(f"feature_value_ids IN {_escape_filter_value_int_list(ids)}")

    if option_value_ids:
        ids = [int(i) for i in option_value_ids]
        field = "option_value_ids_in_stock_outlet" if channel == "outlet" else "option_value_ids_in_stock_normal"
        if len(ids) == 1:
            parts.append(f"{field} = {int(ids[0])}")
        else:
            parts.append(f"{field} IN {_escape_filter_value_int_list(ids)}")

    return " AND ".join([p for p in parts if p])


def search_products_ids(
    *,
    q: str,
    index_uid: str,
    site_id: int | None,
    channel: str,
    in_stock_only: bool,
    category_ancestor_ids: list[int] | None,
    brand_id: int | None,
    group_id: int | None,
    feature_value_ids: list[int] | None,
    option_value_ids: list[int] | None,
    offset: int,
    limit: int,
) -> SearchResult:
    client = MeiliClient()
    if not client.enabled():
        raise MeiliError("Meilisearch is not enabled")

    filter_str = build_meili_filter(
        site_id=site_id,
        channel=channel,
        in_stock_only=in_stock_only,
        category_ancestor_ids=category_ancestor_ids,
        brand_id=brand_id,
        group_id=group_id,
        feature_value_ids=feature_value_ids,
        option_value_ids=option_value_ids,
    )

    payload: dict[str, Any] = {
        "q": str(q),
        "offset": int(offset),
        "limit": int(limit),
        "attributesToRetrieve": ["id"],
    }
    if filter_str:
        payload["filter"] = filter_str

    res = client.search(uid=index_uid, payload=payload)
    hits = res.get("hits") or []
    ids: list[int] = []
    for h in hits:
        try:
            ids.append(int(h.get("id")))
        except Exception:
            continue

    total = res.get("estimatedTotalHits")
    try:
        total_i = int(total) if total is not None else None
    except Exception:
        total_i = None

    return SearchResult(ids=ids, estimated_total=total_i)


def search_products_facets(
    *,
    q: str,
    index_uid: str,
    site_id: int | None,
    channel: str,
    in_stock_only: bool,
    category_ancestor_ids: list[int] | None,
    brand_id: int | None,
    group_id: int | None,
    feature_value_ids: list[int] | None,
    option_value_ids: list[int] | None,
) -> FacetResult:
    client = MeiliClient()
    if not client.enabled():
        raise MeiliError("Meilisearch is not enabled")

    filter_str = build_meili_filter(
        site_id=site_id,
        channel=channel,
        in_stock_only=in_stock_only,
        category_ancestor_ids=category_ancestor_ids,
        brand_id=brand_id,
        group_id=group_id,
        feature_value_ids=feature_value_ids,
        option_value_ids=option_value_ids,
    )

    option_field = "option_value_ids_in_stock_outlet" if channel == "outlet" else "option_value_ids_in_stock_normal"
    payload: dict[str, Any] = {
        "q": str(q),
        "limit": 0,
        "facets": [
            "category_id",
            "brand_id",
            "group_id",
            "feature_value_ids",
            option_field,
        ],
    }
    if filter_str:
        payload["filter"] = filter_str

    res = client.search(uid=index_uid, payload=payload)
    dist = res.get("facetDistribution") or {}
    if not isinstance(dist, dict):
        dist = {}
    return FacetResult(distribution=dist)
