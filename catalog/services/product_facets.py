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
    CategoryFacet,
    Feature,
    OptionType,
    Product,
    ProductFeatureValue,
    ProductGroup,
    VariantFeatureValue,
    VariantOptionValue,
)
from catalog.services.value_groups import value_numeric_range


def _collect_num_stats(
    num_stats: dict, code: str, ranges: list, attr_unit: str
) -> None:
    """Merge (lo, hi, unit) tuples into per-code numeric bounds."""
    for lo, hi, unit in ranges:
        st = num_stats.setdefault(code, {"min": None, "max": None, "unit": ""})
        st["min"] = lo if st["min"] is None else min(st["min"], lo)
        st["max"] = hi if st["max"] is None else max(st["max"], hi)
        st["unit"] = st["unit"] or unit or attr_unit or ""


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
                    "list_template": c.list_template,
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
    # Per canonical code numeric bounds across used values — feeds the
    # 'range' widget facets (CategoryFacet config).
    num_stats: dict[str, dict] = {}
    for f in features_qs:
        used_vals = used_vals_by_feature.get(int(f.id), set())
        # Grouped raw values collapse into their group checkbox; ungrouped
        # values are shown raw. Group ids are emitted as negatives so they
        # never collide with FeatureValue ids.
        groups_seen: dict[int, object] = {}
        ungrouped: list = []
        f_num: list = []
        for v in f.values.all():
            if not v.is_active or v.value not in used_vals:
                continue
            rng = value_numeric_range(v, v.value)
            if rng is not None:
                f_num.append((*rng, v.unit))
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
                "widget": "checkbox",
                "values": values_out,
            }
        )
        _collect_num_stats(num_stats, f.code, f_num, f.unit)

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
        t_num: list = []
        for v in t.values.all():
            if not v.is_active or v.code not in used_codes:
                continue
            rng = value_numeric_range(v, v.label, v.code)
            if rng is not None:
                t_num.append((*rng, v.unit))
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
                "value_num": v.value_num,
                "value_num_max": v.value_num_max,
                "unit": v.unit,
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
                "widget": "checkbox",
                "values": ovals_out,
            }
        )
        _collect_num_stats(num_stats, t.code, t_num, t.unit)

    # Feature ir option type su tuo pačiu canonical code -> VIENAS facetas
    # (option reikšmės suliejamos į feature values, dedupe pagal code).
    feature_by_code = {f["code"]: f for f in features_out}
    merged_option_types = []
    for t in option_types_out:
        target = feature_by_code.get(t["code"])
        if target is None:
            merged_option_types.append(t)
            continue
        seen_vals = {v["value"] for v in target["values"]}
        for v in t["values"]:
            if v["code"] in seen_vals:
                continue
            seen_vals.add(v["code"])
            target["values"].append(
                {
                    "id": int(v["id"]) - 1_000_000,
                    "value": v["code"],
                    "label": v["label"],
                }
            )
    option_types_out = merged_option_types

    # F4: per-category facet configuration. When the selected category has
    # enabled CategoryFacet rows, only those codes are returned — in the
    # configured order, with the configured widget. `source` pins a code to
    # the feature or option side; 'auto' accepts either (same-code facets
    # already merged above). Range facets carry min/max/unit and no values.
    if selected_category is not None:
        facet_rows = list(
            CategoryFacet.objects.filter(
                category_id=int(selected_category.id),
                is_enabled=True,
            ).order_by("order", "code")
        )
    else:
        facet_rows = []

    if facet_rows:
        cfg_by_code = {r.code: r for r in facet_rows}
        order_idx = {r.code: i for i, r in enumerate(facet_rows)}

        def _apply_facet_cfg(entry: dict) -> dict:
            cfg = cfg_by_code[entry["code"]]
            entry["widget"] = cfg.widget
            if cfg.widget == CategoryFacet.Widget.RANGE:
                st = num_stats.get(entry["code"]) or {}
                entry["min"] = st.get("min")
                entry["max"] = st.get("max")
                entry["unit"] = st.get("unit") or ""
                entry["values"] = []
            return entry

        features_out = [
            _apply_facet_cfg(f)
            for f in features_out
            if f["code"] in cfg_by_code
            and cfg_by_code[f["code"]].source != CategoryFacet.Source.OPTION
        ]
        features_out.sort(key=lambda f: order_idx[f["code"]])
        option_types_out = [
            _apply_facet_cfg(t)
            for t in option_types_out
            if t["code"] in cfg_by_code
            and cfg_by_code[t["code"]].source != CategoryFacet.Source.FEATURE
        ]
        option_types_out.sort(key=lambda t: order_idx[t["code"]])

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
