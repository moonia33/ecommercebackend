"""SEO slug'ai prekėms: generuojami iš kiekvienos kalbos pavadinimo.

Pasikeitus pavadinimui slug'as perrašomas, o senasis išsaugomas
`ProductSlugRedirect`, kad senos nuorodos 301 nukreiptų į naują adresą.
"""
from __future__ import annotations

import re

from django.utils.text import slugify

from catalog.models import Product, ProductSlugRedirect, ProductTranslation

PRIMARY_LANGUAGE = "lt"
_MAX_BASE = 240


def slug_base(text: str) -> str:
    return (slugify(text or "")[:_MAX_BASE].strip("-")) or "product"


def slug_matches(current: str, base: str) -> bool:
    """`base` arba `base-N` (unikalumo priesaga) laikomi tuo pačiu slug'u."""
    if not current:
        return False
    return current == base or re.fullmatch(rf"{re.escape(base)}-\d+", current) is not None


def _product_slug_taken(slug: str, product_id: int | None) -> bool:
    return Product.objects.filter(slug=slug).exclude(pk=product_id).exists()


def _translation_slug_taken(slug: str, lang: str, product_id: int | None) -> bool:
    return (
        ProductTranslation.objects.filter(language_code=lang, slug=slug)
        .exclude(product_id=product_id)
        .exists()
    )


def _unique(base: str, taken) -> str:
    candidate, i = base, 2
    while taken(candidate):
        candidate = f"{base}-{i}"
        i += 1
    return candidate


def _remember(product_id: int, lang: str, old_slug: str, new_slug: str) -> None:
    if old_slug and old_slug != new_slug:
        ProductSlugRedirect.objects.update_or_create(
            language_code=lang, slug=old_slug, defaults={"product_id": product_id}
        )
    # Gyvas slug'as visada svarbesnis už seną nukreipimą.
    ProductSlugRedirect.objects.filter(language_code=lang, slug=new_slug).delete()


def assign_product_slug(product: Product, source: str) -> bool:
    """Nustato `Product.slug` (pagrindinė kalba) pagal `source`.

    Grąžina True, jei slug'as pasikeitė. Produktas turi būti išsaugotas
    (turėti pk), kad būtų galima įrašyti nukreipimą.
    """
    base = slug_base(source)
    old = product.slug or ""
    if slug_matches(old, base):
        return False
    new = _unique(base, lambda s: _product_slug_taken(s, product.pk))
    product.slug = new
    if product.pk:
        _remember(product.pk, PRIMARY_LANGUAGE, old, new)
    return True


def assign_translation_slug(tr: ProductTranslation, source: str) -> bool:
    base = slug_base(source)
    old = tr.slug or ""
    if slug_matches(old, base):
        return False
    lang = tr.language_code
    new = _unique(base, lambda s: _translation_slug_taken(s, lang, tr.product_id))
    tr.slug = new
    _remember(tr.product_id, lang, old, new)
    return True


def resolve_redirect(slug: str, language_code: str | None) -> Product | None:
    qs = ProductSlugRedirect.objects.filter(slug=slug).select_related("product")
    rows = list(qs[:10])
    if not rows:
        return None
    lang = (language_code or "").split("-")[0].lower()
    for preferred in (lang, PRIMARY_LANGUAGE):
        for row in rows:
            if row.language_code == preferred:
                return row.product
    return rows[0].product
