from __future__ import annotations

from ninja import Router
from ninja.errors import HttpError

from api.i18n import get_request_language_code, translation_fallback_chain
from api.shop_context import get_request_shop, shop_context_enabled, shop_context_strict

from .models import ShippingCountry, ShippingCountryTranslation, ShopShippingMethodPolicy
from .schemas import ShippingCountryOut, ShippingMethodPublicOut, ShippingMoneyOut


router = Router(tags=["shipping"])


def _pick_best_translation(*, country_id: int, language_code: str, shop=None) -> ShippingCountryTranslation | None:
    langs = translation_fallback_chain(language_code, shop=shop)
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


@router.get("/methods", response=list[ShippingMethodPublicOut])
def shipping_methods_public(request, country_code: str = "LT"):
    """PDP pristatymo blokas: aktyvūs metodai su brutto kainomis per country.

    Tas pats rates/policy/vat skaičiavimas kaip checkout
    /checkout/shipping-methods — tik viešas ir be image/pickup URL'ų.
    """
    from decimal import Decimal

    from django.db import models as dj_models

    from checkout.services import get_shipping_net, get_shipping_tax_class, money_from_net as _mfn
    from pricing.services import get_vat_rate

    from .models import ShippingMethod, ShippingRate

    shop = get_request_shop(request)
    if shop_context_enabled() and shop_context_strict() and shop is None:
        raise HttpError(404, "Shop not resolved for host")

    country_code = (country_code or "").strip().upper()
    if len(country_code) != 2:
        raise HttpError(400, "Invalid country_code")

    tax_class = get_shipping_tax_class()
    vat_rate = Decimal(get_vat_rate(country_code=country_code, tax_class=tax_class)) if tax_class else Decimal("0")

    methods = list(
        ShippingMethod.objects.filter(is_active=True).order_by("sort_order", "code")
    )
    policy_by_method_id = {}
    if shop is not None:
        pols = list(
            ShopShippingMethodPolicy.objects.filter(shop_id=shop.id, is_active=True)
            .filter(dj_models.Q(country_code="") | dj_models.Q(country_code=country_code))
        )
        if pols:
            policy_by_method_id = {int(p.method_id): p for p in pols}
            methods = [m for m in methods if int(m.id) in policy_by_method_id]
            methods.sort(
                key=lambda m: (
                    int(
                        policy_by_method_id[int(m.id)].sort_order_override
                        if policy_by_method_id[int(m.id)].sort_order_override is not None
                        else m.sort_order
                    ),
                    m.code,
                )
            )

    rates = {
        int(r.method_id): r
        for r in ShippingRate.objects.filter(
            is_active=True, method__in=methods, country_code=country_code
        )
    }

    out: list[ShippingMethodPublicOut] = []
    for m in methods:
        if int(m.id) in rates:
            shipping_net = Decimal(rates[int(m.id)].net_eur)
        else:
            try:
                shipping_net = get_shipping_net(
                    shipping_method=m.code, country_code=country_code
                )
            except Exception:
                shipping_net = Decimal("0.00")
        p = policy_by_method_id.get(int(m.id))
        if p and p.net_eur_override is not None:
            shipping_net = Decimal(p.net_eur_override)
        price = _mfn(currency="EUR", unit_net=shipping_net, vat_rate=vat_rate, qty=1)
        out.append(
            ShippingMethodPublicOut(
                code=m.code,
                name=m.name,
                carrier_code=m.carrier_code or "",
                requires_pickup_point=bool(m.requires_pickup_point),
                price=ShippingMoneyOut(
                    currency=price.currency,
                    net=price.net,
                    vat_rate=price.vat_rate,
                    vat=price.vat,
                    gross=price.gross,
                ),
            )
        )
    return out


@router.get("/countries", response=list[ShippingCountryOut])
def shipping_countries(request, language_code: str | None = None):
    if language_code is None:
        language_code = get_request_language_code(request)

    shop = get_request_shop(request)
    if shop_context_enabled() and shop_context_strict() and shop is None:
        raise HttpError(404, "Shop not resolved for host")

    countries = list(
        ShippingCountry.objects.filter(is_active=True)
        .only("id", "code", "sort_order")
        .order_by("sort_order", "code")
    )
    if shop is not None:
        allowed = set(
            ShopShippingMethodPolicy.objects.filter(shop_id=shop.id, is_active=True)
            .exclude(country_code="")
            .values_list("country_code", flat=True)
        )
        if allowed:
            countries = [c for c in countries if (c.code or "").upper() in allowed]

    out: list[ShippingCountryOut] = []
    for c in countries:
        t = _pick_best_translation(country_id=int(c.id), language_code=language_code, shop=shop)
        name = getattr(t, "name", "") or ""
        out.append(ShippingCountryOut(code=str(c.code or "").upper(), name=name))
    return out
