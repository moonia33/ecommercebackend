from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from django.db.models import Case, DecimalField, ExpressionWrapper, F, Min, Q, Value, When

from catalog.models import (
    Category,
    InventoryItem,
    Product,
    ProductFeatureValue,
    SiteBrandExclusion,
    SiteCategoryBrandExclusion,
    SiteCategoryVisibility,
    VariantOptionValue,
)


@dataclass(frozen=True)
class ProductSearchDoc:
    id: int
    sku: str
    slug: str
    name: str
    brand_id: int | None
    group_id: int | None
    category_id: int | None
    category_ancestor_ids: list[int]
    visible_in_site_ids: list[int]
    feature_value_ids: list[int]
    option_value_ids_in_stock_normal: list[int]
    option_value_ids_in_stock_outlet: list[int]
    has_stock_normal: bool
    has_stock_outlet: bool
    min_price_cents_normal: int | None
    min_price_cents_outlet: int | None


def _category_parent_map() -> dict[int, int | None]:
    rows = Category.objects.filter(is_active=True).values("id", "parent_id")
    return {int(r["id"]): (int(r["parent_id"]) if r["parent_id"] is not None else None) for r in rows}


def _ancestor_ids_for_category_id(*, category_id: int | None, parent_of: dict[int, int | None]) -> list[int]:
    if category_id is None:
        return []
    out: list[int] = []
    cur: int | None = int(category_id)
    seen: set[int] = set()
    while cur is not None and cur not in seen:
        out.append(int(cur))
        seen.add(int(cur))
        cur = parent_of.get(int(cur))
    return out


def _descendant_ids_map() -> dict[int, list[int]]:
    rows = Category.objects.filter(is_active=True).values("id", "parent_id")
    children: dict[int, list[int]] = {}
    for r in rows:
        cid = int(r["id"])
        pid = r["parent_id"]
        if pid is None:
            continue
        children.setdefault(int(pid), []).append(cid)
    return children


def _descendant_ids_for_root(*, root_id: int, children: dict[int, list[int]]) -> list[int]:
    out: list[int] = []
    stack = [int(root_id)]
    seen: set[int] = set()
    while stack:
        cid = int(stack.pop())
        if cid in seen:
            continue
        seen.add(cid)
        out.append(cid)
        for ch in children.get(cid, []):
            stack.append(int(ch))
    return out


def _site_allowed_category_ids(*, site_id: int, children: dict[int, list[int]]) -> set[int] | None:
    rules = list(
        SiteCategoryVisibility.objects.filter(site_id=int(site_id), is_active=True)
        .only("category_id", "include_descendants")
    )
    if not rules:
        return None
    allowed: set[int] = set()
    for r in rules:
        cid = int(r.category_id)
        if bool(r.include_descendants):
            allowed.update(_descendant_ids_for_root(root_id=cid, children=children))
        else:
            allowed.add(cid)
    return allowed


def _site_excluded_brand_ids(*, site_id: int) -> set[int]:
    return set(
        int(i)
        for i in SiteBrandExclusion.objects.filter(site_id=int(site_id), is_active=True).values_list("brand_id", flat=True)
    )


def _site_category_excluded_brand_ids(*, site_id: int) -> list[tuple[int, bool, int]]:
    rows = SiteCategoryBrandExclusion.objects.filter(site_id=int(site_id), is_active=True).values(
        "category_id", "include_descendants", "brand_id"
    )
    out: list[tuple[int, bool, int]] = []
    for r in rows:
        out.append((int(r["category_id"]), bool(r["include_descendants"]), int(r["brand_id"])))
    return out


def _excluded_brand_for_category(*, category_id: int, rules: list[tuple[int, bool, int]], children: dict[int, list[int]]) -> set[int]:
    out: set[int] = set()
    for root_id, include_desc, brand_id in rules:
        if include_desc:
            if int(category_id) in set(_descendant_ids_for_root(root_id=int(root_id), children=children)):
                out.add(int(brand_id))
        else:
            if int(category_id) == int(root_id):
                out.add(int(brand_id))
    return out


def _to_cents(v: Decimal | None) -> int | None:
    if v is None:
        return None
    try:
        cents = int((Decimal(v) * Decimal("100")).quantize(Decimal("1")))
    except Exception:
        return None
    return cents


def build_product_search_docs(*, site_ids: list[int] | None = None) -> list[dict]:
    parent_of = _category_parent_map()
    children = _descendant_ids_map()

    if site_ids is None:
        from api.models import Site

        site_ids = list(Site.objects.filter(is_active=True).values_list("id", flat=True))

    allowed_by_site: dict[int, set[int] | None] = {}
    excluded_brand_by_site: dict[int, set[int]] = {}
    category_brand_rules_by_site: dict[int, list[tuple[int, bool, int]]] = {}
    for sid in site_ids:
        allowed_by_site[int(sid)] = _site_allowed_category_ids(site_id=int(sid), children=children)
        excluded_brand_by_site[int(sid)] = _site_excluded_brand_ids(site_id=int(sid))
        category_brand_rules_by_site[int(sid)] = _site_category_excluded_brand_ids(site_id=int(sid))

    vis_normal = InventoryItem.OfferVisibility.NORMAL
    vis_outlet = InventoryItem.OfferVisibility.OUTLET

    offer_price_expr = Case(
        When(variants__inventory_items__never_discount=True, then=F("variants__price_eur")),
        When(
            variants__inventory_items__offer_price_override_eur__isnull=False,
            then=F("variants__inventory_items__offer_price_override_eur"),
        ),
        When(
            variants__inventory_items__offer_discount_percent__isnull=False,
            then=ExpressionWrapper(
                F("variants__price_eur")
                * (Value(100) - F("variants__inventory_items__offer_discount_percent"))
                / Value(100),
                output_field=DecimalField(max_digits=12, decimal_places=2),
            ),
        ),
        default=F("variants__price_eur"),
        output_field=DecimalField(max_digits=12, decimal_places=2),
    )

    def offer_filter_for(visibility: str):
        return (
            Q(variants__is_active=True)
            & Q(variants__inventory_items__offer_visibility=visibility)
            & Q(variants__inventory_items__qty_on_hand__gt=F("variants__inventory_items__qty_reserved"))
        )

    base_qs = (
        Product.objects.filter(is_active=True)
        .select_related("brand", "category", "group")
        .annotate(_min_offer_price_normal=Min(offer_price_expr, filter=offer_filter_for(vis_normal)))
        .annotate(_min_offer_price_outlet=Min(offer_price_expr, filter=offer_filter_for(vis_outlet)))
    )

    docs: list[dict] = []

    for p in base_qs.iterator(chunk_size=200):
        pid = int(p.id)
        category_id = int(p.category_id) if p.category_id else None
        brand_id = int(p.brand_id) if p.brand_id else None
        group_id = int(getattr(p, "group_id", None) or 0) or None

        category_ancestor_ids = _ancestor_ids_for_category_id(category_id=category_id, parent_of=parent_of)

        visible_in_site_ids: list[int] = []
        for sid in site_ids:
            sid_i = int(sid)
            allowed = allowed_by_site.get(sid_i)
            if allowed is not None:
                if category_id is None or int(category_id) not in allowed:
                    continue
            excluded_brand = excluded_brand_by_site.get(sid_i, set())
            if brand_id is not None and int(brand_id) in excluded_brand:
                continue
            if brand_id is not None and category_id is not None:
                cat_rules = category_brand_rules_by_site.get(sid_i, [])
                excluded_for_cat = _excluded_brand_for_category(category_id=int(category_id), rules=cat_rules, children=children)
                if int(brand_id) in excluded_for_cat:
                    continue
            visible_in_site_ids.append(sid_i)

        feature_value_ids = list(
            ProductFeatureValue.objects.filter(product_id=pid)
            .values_list("feature_value_id", flat=True)
            .distinct()
        )
        feature_value_ids = [int(i) for i in feature_value_ids]

        variant_ids_normal = list(
            InventoryItem.objects.filter(
                variant__product_id=pid,
                variant__is_active=True,
                offer_visibility=vis_normal,
                qty_on_hand__gt=F("qty_reserved"),
            )
            .values_list("variant_id", flat=True)
            .distinct()
        )
        variant_ids_outlet = list(
            InventoryItem.objects.filter(
                variant__product_id=pid,
                variant__is_active=True,
                offer_visibility=vis_outlet,
                qty_on_hand__gt=F("qty_reserved"),
            )
            .values_list("variant_id", flat=True)
            .distinct()
        )

        option_value_ids_normal = list(
            VariantOptionValue.objects.filter(variant_id__in=variant_ids_normal)
            .values_list("option_value_id", flat=True)
            .distinct()
        )
        option_value_ids_outlet = list(
            VariantOptionValue.objects.filter(variant_id__in=variant_ids_outlet)
            .values_list("option_value_id", flat=True)
            .distinct()
        )

        docs.append(
            {
                "id": pid,
                "sku": str(p.sku or ""),
                "slug": str(p.slug or ""),
                "name": str(p.name or ""),
                "brand_id": brand_id,
                "group_id": group_id,
                "category_id": category_id,
                "category_ancestor_ids": [int(i) for i in category_ancestor_ids],
                "visible_in_site_ids": [int(i) for i in visible_in_site_ids],
                "feature_value_ids": [int(i) for i in feature_value_ids],
                "option_value_ids_in_stock_normal": [int(i) for i in option_value_ids_normal],
                "option_value_ids_in_stock_outlet": [int(i) for i in option_value_ids_outlet],
                "has_stock_normal": bool(getattr(p, "_min_offer_price_normal", None) is not None),
                "has_stock_outlet": bool(getattr(p, "_min_offer_price_outlet", None) is not None),
                "min_price_cents_normal": _to_cents(getattr(p, "_min_offer_price_normal", None)),
                "min_price_cents_outlet": _to_cents(getattr(p, "_min_offer_price_outlet", None)),
            }
        )

    return docs


def meili_products_settings() -> dict:
    return {
        "searchableAttributes": ["name", "sku", "slug"],
        "filterableAttributes": [
            "visible_in_site_ids",
            "category_id",
            "category_ancestor_ids",
            "brand_id",
            "group_id",
            "feature_value_ids",
            "option_value_ids_in_stock_normal",
            "option_value_ids_in_stock_outlet",
            "has_stock_normal",
            "has_stock_outlet",
            "min_price_cents_normal",
            "min_price_cents_outlet",
        ],
        "sortableAttributes": [
            "min_price_cents_normal",
            "min_price_cents_outlet",
        ],
        "rankingRules": [
            "words",
            "typo",
            "proximity",
            "attribute",
            "sort",
            "exactness",
        ],
    }
