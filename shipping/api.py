from __future__ import annotations

from ninja import Router

from api.i18n import get_request_language_code, translation_fallback_chain

from .models import ShippingCountry, ShippingCountryTranslation
from .schemas import ShippingCountryOut


router = Router(tags=["shipping"])


def _pick_best_translation(*, country_id: int, language_code: str) -> ShippingCountryTranslation | None:
    langs = translation_fallback_chain(language_code)
    rows = list(
        ShippingCountryTranslation.objects.filter(
            shipping_country_id=country_id,
            language_code__in=langs,
        ).only("language_code", "name", "shipping_country_id")
    )
    order_index = {lang: i for i, lang in enumerate(langs)}
    best = None
    best_idx = 10_000
    for r in rows:
        idx = order_index.get((r.language_code or "").lower(), 10_000)
        if idx < best_idx:
            best = r
            best_idx = idx
    return best


@router.get("/countries", response=list[ShippingCountryOut])
def shipping_countries(request, language_code: str | None = None):
    if language_code is None:
        language_code = get_request_language_code(request)

    countries = list(
        ShippingCountry.objects.filter(is_active=True)
        .only("id", "code", "sort_order")
        .order_by("sort_order", "code")
    )

    out: list[ShippingCountryOut] = []
    for c in countries:
        t = _pick_best_translation(country_id=int(c.id), language_code=language_code)
        name = getattr(t, "name", "") or ""
        out.append(ShippingCountryOut(code=str(c.code or "").upper(), name=name))
    return out
