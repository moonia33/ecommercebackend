from __future__ import annotations

from catalog.api_schemas import FeatureOut, OptionTypeOut
from catalog.localization import (
    localize_brand,
    localize_category,
    localize_feature,
    localize_feature_value,
    localize_option_type,
    localize_option_value,
)
from catalog.models import (
    Brand,
    Category,
    Feature,
    OptionType,
    Product,
    ProductFeatureValue,
    ProductGroup,
    VariantFeatureValue,
    VariantOptionValue,
)


def build_facets_payload(*, product_ids, selected_category: Category | None, language_code: str | None = None):
    if not product_ids:
        return {
            "categories": [],
            "brands": [],
            "product_groups": [],
            "features": [],
            "option_types": [],
        }

    if selected_category:
        cat_qs = Category.objects.filter(is_active=True, parent_id=selected_category.id).prefetch_related("translations").order_by("name")
    else:
        cat_qs = Category.objects.filter(is_active=True, parent_id__isnull=True).prefetch_related("translations").order_by("name")

    categories_out = []
    cat_ids = list(cat_qs.values_list("id", flat=True))
    if cat_ids:
        product_category_ids = set(
            Product.objects.filter(is_active=True, id__in=product_ids)
            .exclude(category_id__isnull=True)
            .values_list("category_id", flat=True)
        )
        # One query for the whole tree; walk parents in memory instead of
        # recursive descendant lookups per root category.
        parent_of = dict(
            Category.objects.filter(is_active=True).values_list("id", "parent_id")
        )

        cat_id_set = {int(cid) for cid in cat_ids}

        def _child_ancestor(cat_id: int) -> int | None:
            cur = cat_id
            seen: set[int] = set()
            while cur in parent_of and cur not in seen:
                seen.add(cur)
                if cur in cat_id_set:
                    return cur
                parent = parent_of.get(cur)
                if not parent:
                    return None
                cur = parent
            return None

        allowed: set[int] = set()
        for cid in product_category_ids:
            top = _child_ancestor(int(cid))
            if top is not None:
                allowed.add(top)

        for c in cat_qs:
            if int(c.id) not in allowed:
                continue
            category_loc = localize_category(c, language_code=language_code)
            categories_out.append(
                {
                    "id": c.id,
                    "slug": category_loc["slug"],
                    "name": category_loc["name"],
                    "parent_id": c.parent_id,
                    "description": category_loc["description"],
                    "hero_image_url": (c.hero_url or None),
                    "menu_icon_url": (c.menu_icon_url_resolved or None),
                    "seo_title": category_loc["seo_title"],
                    "seo_description": category_loc["seo_description"],
                    "seo_keywords": category_loc["seo_keywords"],
                }
            )

    brands_qs = Brand.objects.filter(is_active=True, products__id__in=product_ids).prefetch_related("translations").distinct().order_by("name")
    groups_qs = ProductGroup.objects.filter(is_active=True, products__id__in=product_ids).distinct().order_by("name")

    feature_ids = set(
        ProductFeatureValue.objects.filter(product_id__in=product_ids)
        .values_list("feature_id", flat=True)
        .distinct()
    ) | set(
        VariantFeatureValue.objects.filter(variant__product_id__in=product_ids)
        .values_list("feature_id", flat=True)
        .distinct()
    )
    features_qs = Feature.objects.filter(is_active=True, is_filterable=True, id__in=feature_ids).prefetch_related("translations", "values", "values__translations", "values__groups").order_by("sort_order", "code")
    # Single grouped query for all feature values used by the filtered products —
    # product-level rows plus variant-level rows (values differ per variant).
    used_vals_by_feature: dict[int, set[str]] = {}
    for fid, val in (
        ProductFeatureValue.objects.filter(product_id__in=product_ids)
        .values_list("feature_id", "feature_value__value")
        .distinct()
    ):
        used_vals_by_feature.setdefault(int(fid), set()).add(val)
    for fid, val in (
        VariantFeatureValue.objects.filter(variant__product_id__in=product_ids)
        .values_list("feature_id", "feature_value__value")
        .distinct()
    ):
        used_vals_by_feature.setdefault(int(fid), set()).add(val)
    features_out: list[FeatureOut] = []
    for f in features_qs:
        used_vals = used_vals_by_feature.get(int(f.id), set())
        # Grouped raw values collapse into their group checkbox; ungrouped
        # values are shown raw. Group ids are emitted as negatives so they
        # never collide with FeatureValue ids.
        groups_seen: dict[int, object] = {}
        ungrouped: list = []
        for v in f.values.all():
            if not v.is_active or v.value not in used_vals:
                continue
            matched = [g for g in v.groups.all() if g.is_active]
            if matched:
                for g in matched:
                    groups_seen[int(g.id)] = g
            else:
                ungrouped.append(v)
        ungrouped.sort(key=lambda v: (v.sort_order, v.value, v.id))
        values_out = [
            {"id": -int(g.id), "value": g.code, "label": g.label}
            for g in sorted(groups_seen.values(), key=lambda g: (g.sort_order, g.code))
        ] + [
            {
                "id": v.id,
                "value": v.value,
                "label": localize_feature_value(v, language_code=language_code),
            }
            for v in ungrouped
        ]
        if not values_out:
            continue
        features_out.append(
            {
                "id": f.id,
                "code": f.code,
                "name": localize_feature(f, language_code=language_code),
                "values": values_out,
            }
        )

    option_type_ids = list(
        VariantOptionValue.objects.filter(variant__product_id__in=product_ids)
        .values_list("option_type_id", flat=True)
        .distinct()
    )
    option_types_qs = OptionType.objects.filter(is_active=True, id__in=option_type_ids).prefetch_related("translations", "values", "values__translations", "values__groups").order_by("sort_order", "code")
    used_codes_by_type: dict[int, set[str]] = {}
    for tid, code in (
        VariantOptionValue.objects.filter(variant__product_id__in=product_ids)
        .values_list("option_type_id", "option_value__code")
        .distinct()
    ):
        used_codes_by_type.setdefault(int(tid), set()).add(code)
    option_types_out: list[OptionTypeOut] = []
    for t in option_types_qs:
        used_codes = used_codes_by_type.get(int(t.id), set())
        ogroups_seen: dict[int, object] = {}
        oungrouped: list = []
        for v in t.values.all():
            if not v.is_active or v.code not in used_codes:
                continue
            matched = [g for g in v.groups.all() if g.is_active]
            if matched:
                for g in matched:
                    ogroups_seen[int(g.id)] = g
            else:
                oungrouped.append(v)
        oungrouped.sort(key=lambda v: (v.sort_order, v.label, v.id))
        ovals_out = [
            {"id": -int(g.id), "code": g.code, "label": g.label}
            for g in sorted(ogroups_seen.values(), key=lambda g: (g.sort_order, g.code))
        ] + [
            {
                "id": v.id,
                "code": v.code,
                "label": localize_option_value(v, language_code=language_code),
            }
            for v in oungrouped
        ]
        if not ovals_out:
            continue
        option_types_out.append(
            {
                "id": t.id,
                "code": t.code,
                "name": localize_option_type(t, language_code=language_code),
                "display_type": t.display_type,
                "swatch_type": t.swatch_type,
                "values": ovals_out,
            }
        )

    brands_out = []
    for b in brands_qs:
        brand_loc = localize_brand(b, language_code=language_code)
        brands_out.append({"id": b.id, "slug": brand_loc["slug"], "name": brand_loc["name"]})

    return {
        "categories": categories_out,
        "brands": brands_out,
        "product_groups": [{"id": g.id, "code": g.code, "name": g.name, "description": g.description or ""} for g in groups_qs],
        "features": features_out,
        "option_types": option_types_out,
    }
