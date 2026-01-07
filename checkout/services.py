from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from django.conf import settings

from catalog.models import TaxClass
from pricing.services import compute_vat, get_vat_rate


@dataclass(frozen=True)
class Money:
    currency: str
    net: Decimal
    vat_rate: Decimal
    vat: Decimal
    gross: Decimal


def money_from_net(*, currency: str, unit_net: Decimal, vat_rate: Decimal, qty: int = 1) -> Money:
    breakdown = compute_vat(unit_net=Decimal(unit_net),
                            vat_rate=Decimal(vat_rate), qty=int(qty))
    return Money(
        currency=currency,
        net=breakdown.total_net,
        vat_rate=breakdown.vat_rate,
        vat=breakdown.total_vat,
        gross=breakdown.total_gross,
    )


def get_shipping_net(*, shipping_method: str, country_code: str) -> Decimal:
    from shipping.models import ShippingRate

    shipping_method = (shipping_method or "").strip()
    country_code = (country_code or "").strip().upper()

    if not shipping_method:
        raise ValueError("shipping_method is required")
    if len(country_code) != 2:
        raise ValueError("Invalid country_code")

    rate = (
        ShippingRate.objects.select_related("method")
        .filter(
            method__code=shipping_method,
            method__is_active=True,
            is_active=True,
            country_code=country_code,
        )
        .first()
    )
    if rate:
        return Decimal(rate.net_eur)

    # Backward-compatible fallback for older setups.
    if shipping_method == "lpexpress":
        return Decimal(str(getattr(settings, "LPEXPRESS_SHIPPING_NET_EUR", "0.00")))

    raise ValueError("Unsupported shipping_method")


def get_shipping_tax_class() -> TaxClass | None:
    # MVP: use standard tax class for shipping VAT.
    code = getattr(settings, "DEFAULT_SHIPPING_TAX_CLASS_CODE", "standard")
    from catalog.models import TaxClass

    return TaxClass.objects.filter(code=code).first()


def get_vat_rate_for(*, country_code: str, tax_class: TaxClass) -> Decimal:
    return get_vat_rate(country_code=country_code, tax_class=tax_class)
