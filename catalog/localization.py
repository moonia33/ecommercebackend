from __future__ import annotations

from django.db.models import Case, IntegerField, Value, When

from api.i18n import get_default_language_code, translation_fallback_chain

from .models import (
    Brand,
    BrandTranslation,
    Category,
    CategoryTranslation,
    Feature,
    FeatureValue,
    OptionType,
    OptionValue,
    Product,
    ProductTranslation,
)


def _best_translation(rows, *, language_code: str | None):
    langs = translation_fallback_chain(language_code)
    if not langs:
        return None
    order_index = {lang: i for i, lang in enumerate(langs)}
    # Base fields already carry the base-language content, so they behave like an
    # implicit translation row at the base language's chain position. A stored
    # translation must outrank that slot to win; otherwise callers use base fields.
    base_rank = order_index.get(get_default_language_code(), 10_000)
    best = None
    best_idx = base_rank
    for row in rows:
        idx = order_index.get((getattr(row, "language_code", "") or "").lower(), 10_000)
        if idx < best_idx:
            best = row
            best_idx = idx
    return best


def _best_translation_from_queryset(qs, *, language_code: str | None):
    langs = translation_fallback_chain(language_code)
    if not langs:
        return None
    when = [When(language_code=lang, then=Value(i)) for i, lang in enumerate(langs)]
    return (
        qs.filter(language_code__in=langs)
        .annotate(_lang_rank=Case(*when, default=Value(10_000), output_field=IntegerField()))
        .order_by("_lang_rank", "id")
        .first()
    )


def localize_category(category: Category, *, language_code: str | None) -> dict[str, str]:
    translation = _best_translation(category.translations.all(), language_code=language_code)
    if translation is None:
        return {
            "name": category.name,
            "slug": category.slug,
            "description": category.description or "",
            "seo_title": category.seo_title or "",
            "seo_description": category.seo_description or "",
            "seo_keywords": category.seo_keywords or "",
        }
    return {
        "name": (translation.name or category.name),
        "slug": (translation.slug or category.slug),
        "description": (translation.description or category.description or ""),
        "seo_title": (translation.seo_title or category.seo_title or ""),
        "seo_description": (translation.seo_description or category.seo_description or ""),
        "seo_keywords": (translation.seo_keywords or category.seo_keywords or ""),
    }


def localize_brand(brand: Brand, *, language_code: str | None) -> dict[str, str]:
    translation = _best_translation(brand.translations.all(), language_code=language_code)
    if translation is None:
        return {
            "name": brand.name,
            "slug": brand.slug,
            "description": brand.description or "",
            "seo_title": brand.seo_title or "",
            "seo_description": brand.seo_description or "",
            "seo_keywords": brand.seo_keywords or "",
        }
    return {
        "name": (translation.name or brand.name),
        "slug": (translation.slug or brand.slug),
        "description": (translation.description or brand.description or ""),
        "seo_title": (translation.seo_title or brand.seo_title or ""),
        "seo_description": (translation.seo_description or brand.seo_description or ""),
        "seo_keywords": (translation.seo_keywords or brand.seo_keywords or ""),
    }


def localize_product(product: Product, *, language_code: str | None) -> dict[str, str]:
    translation = _best_translation(product.translations.all(), language_code=language_code)
    if translation is None:
        return {
            "name": product.name,
            "slug": product.slug,
            "description": product.description or "",
            "seo_title": product.seo_title or "",
            "seo_description": product.seo_description or "",
            "seo_keywords": product.seo_keywords or "",
        }
    return {
        "name": (translation.name or product.name),
        "slug": (translation.slug or product.slug),
        "description": (translation.description or product.description or ""),
        "seo_title": (translation.seo_title or product.seo_title or ""),
        "seo_description": (translation.seo_description or product.seo_description or ""),
        "seo_keywords": (translation.seo_keywords or product.seo_keywords or ""),
    }


def localize_feature(feature: Feature, *, language_code: str | None) -> str:
    translation = _best_translation(feature.translations.all(), language_code=language_code)
    if translation is None:
        return feature.name
    return translation.name or feature.name


def localize_feature_value(feature_value: FeatureValue, *, language_code: str | None) -> str:
    translation = _best_translation(feature_value.translations.all(), language_code=language_code)
    if translation is None:
        return feature_value.label or feature_value.value
    return translation.value or feature_value.label or feature_value.value


def localize_option_type(option_type: OptionType, *, language_code: str | None) -> str:
    translation = _best_translation(option_type.translations.all(), language_code=language_code)
    if translation is None:
        return option_type.name
    return translation.name or option_type.name


def localize_option_value(option_value: OptionValue, *, language_code: str | None) -> str:
    translation = _best_translation(option_value.translations.all(), language_code=language_code)
    if translation is None:
        return option_value.label
    return translation.label or option_value.label


def resolve_category_by_slug(*, slug: str, language_code: str | None, is_active: bool = True) -> Category | None:
    if not slug:
        return None
    base = Category.objects.filter(slug=slug)
    if is_active:
        base = base.filter(is_active=True)
    row = base.first()
    if row is not None:
        return row

    translation = _best_translation_from_queryset(
        CategoryTranslation.objects.filter(slug=slug).select_related("category"),
        language_code=language_code,
    )
    if translation is None:
        return None
    category = translation.category
    if is_active and not bool(category.is_active):
        return None
    return category


def resolve_brand_by_slug(*, slug: str, language_code: str | None, is_active: bool = True) -> Brand | None:
    if not slug:
        return None
    base = Brand.objects.filter(slug=slug)
    if is_active:
        base = base.filter(is_active=True)
    row = base.first()
    if row is not None:
        return row

    translation = _best_translation_from_queryset(
        BrandTranslation.objects.filter(slug=slug).select_related("brand"),
        language_code=language_code,
    )
    if translation is None:
        return None
    brand = translation.brand
    if is_active and not bool(brand.is_active):
        return None
    return brand


def resolve_product_by_slug(*, slug: str, language_code: str | None, is_active: bool = True):
    if not slug:
        return None
    base = Product.objects.filter(slug=slug)
    if is_active:
        base = base.filter(is_active=True)
    row = base.first()
    if row is not None:
        return row

    translation = _best_translation_from_queryset(
        ProductTranslation.objects.filter(slug=slug).select_related("product"),
        language_code=language_code,
    )
    if translation is None:
        return None
    product = translation.product
    if is_active and not bool(product.is_active):
        return None
    return product
