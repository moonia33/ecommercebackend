from __future__ import annotations

from ninja.errors import HttpError

from .shared import (
    _shop_or_404_when_strict,
    apply_shop_product_scope,
    get_request_language_code,
    localize_brand,
    localize_category,
    localize_feature,
    localize_feature_value,
    localize_option_type,
    localize_option_value,
    resolve_brand_by_slug,
    resolve_category_by_slug,
    router,
)
from ..models import Brand, Category, Feature, OptionType, Product, ProductGroup
from ..api_schemas import (
    BrandOut,
    CategoryDetailOut,
    CategoryOut,
    FeatureOut,
    OptionTypeOut,
    ProductGroupOut,
)


@router.get("/categories", response=list[CategoryOut])
def categories(request):
    language_code = get_request_language_code(request)
    shop = _shop_or_404_when_strict(request)
    scoped_ids = apply_shop_product_scope(
        qs=Product.objects.filter(is_active=True),
        shop_id=(int(shop.id) if shop else None),
    )
    used_category_ids = set(
        scoped_ids.values_list("category_id", flat=True).distinct()
    )
    qs = Category.objects.filter(is_active=True).prefetch_related("translations").order_by("name")
    out = []
    for c in qs:
        if used_category_ids and c.id not in used_category_ids:
            continue
        loc = localize_category(c, language_code=language_code)
        out.append(
            {
                "id": c.id,
                "slug": loc["slug"],
                "name": loc["name"],
                "parent_id": c.parent_id,
                "description": loc["description"],
                "hero_image_url": (c.hero_url or None),
                "menu_icon_url": (c.menu_icon_url_resolved or None),
                "seo_title": loc["seo_title"],
                "seo_description": loc["seo_description"],
                "seo_keywords": loc["seo_keywords"],
            }
        )
    return out


@router.get("/categories/{slug}", response=CategoryDetailOut)
def category_detail(request, slug: str, language_code: str | None = None):
    if language_code is None:
        language_code = get_request_language_code(request)
    c = resolve_category_by_slug(slug=slug, language_code=language_code, is_active=True)
    if c:
        c = Category.objects.filter(id=c.id).select_related("parent").prefetch_related("translations").first()
    if not c:
        raise HttpError(404, "Category not found")
    loc = localize_category(c, language_code=language_code)
    return {
        "id": c.id,
        "slug": loc["slug"],
        "name": loc["name"],
        "parent_id": c.parent_id,
        "description": loc["description"],
        "hero_image_url": (c.hero_url or None),
        "menu_icon_url": (c.menu_icon_url_resolved or None),
        "seo_title": loc["seo_title"],
        "seo_description": loc["seo_description"],
        "seo_keywords": loc["seo_keywords"],
    }


@router.get("/brands", response=list[BrandOut])
def brands(request):
    language_code = get_request_language_code(request)
    shop = _shop_or_404_when_strict(request)
    products_qs = apply_shop_product_scope(
        qs=Product.objects.filter(is_active=True),
        shop_id=(int(shop.id) if shop else None),
    )
    qs = Brand.objects.filter(
        is_active=True, products__in=products_qs).prefetch_related("translations").distinct().order_by("name")
    out = []
    for b in qs:
        loc = localize_brand(b, language_code=language_code)
        out.append({"id": b.id, "slug": loc["slug"], "name": loc["name"]})
    return out


@router.get("/brands/{slug}", response=BrandOut)
def brand_detail(request, slug: str, language_code: str | None = None):
    if language_code is None:
        language_code = get_request_language_code(request)
    b = resolve_brand_by_slug(slug=slug, language_code=language_code, is_active=True)
    if b:
        b = Brand.objects.filter(id=b.id).prefetch_related("translations").first()
    if not b:
        raise HttpError(404, "Brand not found")
    loc = localize_brand(b, language_code=language_code)
    return {"id": b.id, "slug": loc["slug"], "name": loc["name"]}


@router.get("/product-groups", response=list[ProductGroupOut])
def product_groups(request):
    qs = ProductGroup.objects.filter(is_active=True).order_by("name")
    return [
        {
            "id": g.id,
            "code": g.code,
            "name": g.name,
            "description": g.description or "",
        }
        for g in qs
    ]


@router.get("/product-groups/{code}", response=ProductGroupOut)
def product_group_detail(request, code: str):
    g = ProductGroup.objects.filter(code=code, is_active=True).first()
    if not g:
        raise HttpError(404, "Product group not found")
    return {"id": g.id, "code": g.code, "name": g.name, "description": g.description or ""}


@router.get("/features", response=list[FeatureOut])
def features(request):
    language_code = get_request_language_code(request)
    qs = (
        Feature.objects.filter(is_active=True, is_filterable=True)
        .prefetch_related("translations", "values", "values__translations")
        .order_by("sort_order", "code")
    )
    out: list[FeatureOut] = []
    for f in qs:
        vals = [v for v in f.values.all() if v.is_active]
        vals.sort(key=lambda v: (v.sort_order, v.value, v.id))
        out.append(
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
    return out


@router.get("/option-types", response=list[OptionTypeOut])
def option_types(request):
    language_code = get_request_language_code(request)
    qs = (
        OptionType.objects.filter(is_active=True)
        .prefetch_related("translations", "values", "values__translations")
        .order_by("sort_order", "code")
    )
    out: list[OptionTypeOut] = []
    for t in qs:
        vals = [v for v in t.values.all() if v.is_active]
        vals.sort(key=lambda v: (v.sort_order, v.label, v.id))
        out.append(
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
                        "value_num": v.value_num,
                        "value_num_max": v.value_num_max,
                        "unit": v.unit,
                    }
                    for v in vals
                ],
            }
        )
    return out
