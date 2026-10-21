from __future__ import annotations

from django.db.models import Q, QuerySet
from ninja.errors import HttpError

from catalog.localization import resolve_brand_by_slug, resolve_category_by_slug
from catalog.models import Category, FeatureValue, OptionValue, Product, ProductGroup
from .catalog_core import descendant_category_ids, parse_pairs, parse_range_pairs
from .value_groups import value_numeric_range


def _range_value_ids(
    *, code: str, lo, hi
) -> tuple[list[int], list[int]]:
    """FeatureValue/OptionValue ids under `code` whose numeric range
    overlaps [lo, hi] (open bounds allowed — None = unbounded).

    Numeric bounds come from value_num/value_num_max when imported, else
    a clean numeric parse of the raw value/label.
    """
    def overlaps(rng) -> bool:
        if rng is None:
            return False
        v_lo, v_hi = rng
        if hi is not None and v_lo > hi:
            return False
        if lo is not None and v_hi < lo:
            return False
        return True

    fv_ids = [
        v.id
        for v in FeatureValue.objects.filter(
            feature__code=code, is_active=True, feature__is_active=True
        )
        if overlaps(value_numeric_range(v, v.value))
    ]
    ov_ids = [
        v.id
        for v in OptionValue.objects.filter(
            option_type__code=code, is_active=True, option_type__is_active=True
        )
        if overlaps(value_numeric_range(v, v.label, v.code))
    ]
    return fv_ids, ov_ids


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
    range_filter: str | None = None,
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
            (
                Q(feature_values__feature_value__value=f_val)
                | Q(feature_values__feature_value__groups__code=f_val)
            )
            & Q(feature_values__feature__code=f_code)
            | (
                Q(variants__feature_values__feature_value__value=f_val)
                | Q(variants__feature_values__feature_value__groups__code=f_val)
            )
            & Q(variants__feature_values__feature__code=f_code)
            # Facetai merge'inami pagal code: ta pati reikšmė gali gyventi
            # kaip option value su tuo pačiu tipu-kodu.
            | (
                Q(variants__option_values__option_value__code=f_val)
                | Q(variants__option_values__option_value__groups__code=f_val)
            )
            & Q(variants__option_values__option_type__code=f_code)
        )

    for o_type, o_val in parse_pairs(option):
        qs = qs.filter(
            Q(variants__option_values__option_value__code=o_val)
            | Q(variants__option_values__option_value__groups__code=o_val),
            variants__option_values__option_type__code=o_type,
        )

    # `range=<code>:<min>-<max>` — overlap semantics: a product matches when
    # any of its feature/option numeric ranges under `code` intersects the
    # requested window (a '10-40 g' rod matches 'weight:20-50').
    for r_code, r_lo, r_hi in parse_range_pairs(range_filter):
        fv_ids, ov_ids = _range_value_ids(code=r_code, lo=r_lo, hi=r_hi)
        if not fv_ids and not ov_ids:
            qs = qs.none()
            continue
        qs = qs.filter(
            Q(feature_values__feature_value_id__in=fv_ids)
            | Q(variants__feature_values__feature_value_id__in=fv_ids)
            | Q(variants__option_values__option_value_id__in=ov_ids)
        )

    if feature or option or range_filter:
        qs = qs.distinct()

    return qs, selected_category
