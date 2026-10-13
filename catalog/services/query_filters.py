from __future__ import annotations

from django.db.models import Q, QuerySet
from ninja.errors import HttpError

from catalog.localization import resolve_brand_by_slug, resolve_category_by_slug
from catalog.models import Category, Product, ProductGroup
from .catalog_core import descendant_category_ids, parse_pairs


def apply_catalog_filters(
    *,
    qs: QuerySet[Product],
    q: str | None = None,
    category_slug: str | None = None,
    brand_slug: str | None = None,
    language_code: str | None = None,
    group_code: str | None = None,
    feature: str | None = None,
    option: str | None = None,
) -> tuple[QuerySet[Product], Category | None]:
    selected_category: Category | None = None

    if q:
        qv = q.strip()
        if qv:
            qs = qs.filter(Q(name__icontains=qv) | Q(slug__icontains=qv) | Q(sku__icontains=qv))

    if category_slug:
        selected_category = resolve_category_by_slug(
            slug=category_slug,
            language_code=language_code,
            is_active=True,
        )
        if not selected_category:
            raise HttpError(404, "Category not found")
        ids = descendant_category_ids(root_id=int(selected_category.id))
        qs = qs.filter(category_id__in=ids)

    if brand_slug:
        brand = resolve_brand_by_slug(
            slug=brand_slug,
            language_code=language_code,
            is_active=True,
        )
        if not brand:
            raise HttpError(404, "Brand not found")
        qs = qs.filter(brand_id=brand.id)

    if group_code:
        group = ProductGroup.objects.filter(code=group_code, is_active=True).first()
        if not group:
            raise HttpError(404, "Product group not found")
        qs = qs.filter(group_id=group.id)

    for f_code, f_val in parse_pairs(feature):
        qs = qs.filter(
            feature_values__feature__code=f_code,
            feature_values__feature_value__value=f_val,
        )

    for o_type, o_val in parse_pairs(option):
        qs = qs.filter(
            variants__option_values__option_type__code=o_type,
            variants__option_values__option_value__code=o_val,
        )

    if feature or option:
        qs = qs.distinct()

    return qs, selected_category
