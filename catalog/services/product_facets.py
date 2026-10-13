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
    VariantOptionValue,
)
from catalog.services.catalog_core import descendant_category_ids


def build_facets_payload(*, product_ids: list[int], selected_category: Category | None, language_code: str | None = None):
    if not product_ids:
        return {
            "categories": [],
            "brands": [],
            "product_groups": [],
            "features": [],
            "option_types": [],
        }

    if selected_category:
        cat_qs = Category.objects.filter(is_active=True, parent_id=selected_category.id).order_by("name")
    else:
        cat_qs = Category.objects.filter(is_active=True, parent_id__isnull=True).order_by("name")

    categories_out = []
    cat_ids = list(cat_qs.values_list("id", flat=True))
    if cat_ids:
        product_category_ids = set(
            Product.objects.filter(is_active=True, id__in=product_ids)
            .exclude(category_id__isnull=True)
            .values_list("category_id", flat=True)
        )
        allowed: set[int] = set()
        for child_id in cat_ids:
            desc_ids = set(descendant_category_ids(root_id=int(child_id)))
            if product_category_ids.intersection(desc_ids):
                allowed.add(int(child_id))

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

    brands_qs = Brand.objects.filter(is_active=True, products__id__in=product_ids).distinct().order_by("name")
    groups_qs = ProductGroup.objects.filter(is_active=True, products__id__in=product_ids).distinct().order_by("name")

    feature_ids = list(
        ProductFeatureValue.objects.filter(product_id__in=product_ids)
        .values_list("feature_id", flat=True)
        .distinct()
    )
    features_qs = Feature.objects.filter(is_active=True, is_filterable=True, id__in=feature_ids).prefetch_related("translations", "values", "values__translations").order_by("sort_order", "code")
    features_out: list[FeatureOut] = []
    for f in features_qs:
        used_vals = set(
            ProductFeatureValue.objects.filter(product_id__in=product_ids, feature_id=f.id).values_list("feature_value__value", flat=True)
        )
        vals = [v for v in f.values.all() if v.is_active and v.value in used_vals]
        vals.sort(key=lambda v: (v.sort_order, v.value, v.id))
        features_out.append(
            {
                "id": f.id,
                "code": f.code,
                "name": localize_feature(f, language_code=language_code),
                "values": [
                    {
                        "id": v.id,
                        "value": v.value,
                        "label": localize_feature_value(v, language_code=language_code),
                    }
                    for v in vals
                ],
            }
        )

    option_type_ids = list(
        VariantOptionValue.objects.filter(variant__product_id__in=product_ids)
        .values_list("option_type_id", flat=True)
        .distinct()
    )
    option_types_qs = OptionType.objects.filter(is_active=True, id__in=option_type_ids).prefetch_related("translations", "values", "values__translations").order_by("sort_order", "code")
    option_types_out: list[OptionTypeOut] = []
    for t in option_types_qs:
        used_codes = set(
            VariantOptionValue.objects.filter(variant__product_id__in=product_ids, option_type_id=t.id)
            .values_list("option_value__code", flat=True)
            .distinct()
        )
        vals = [v for v in t.values.all() if v.is_active and v.code in used_codes]
        vals.sort(key=lambda v: (v.sort_order, v.label, v.id))
        option_types_out.append(
            {
                "id": t.id,
                "code": t.code,
                "name": localize_option_type(t, language_code=language_code),
                "display_type": t.display_type,
                "swatch_type": t.swatch_type,
                "values": [
                    {
                        "id": v.id,
                        "code": v.code,
                        "label": localize_option_value(v, language_code=language_code),
                    }
                    for v in vals
                ],
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
