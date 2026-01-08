from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from django.conf import settings
from django.db import models

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


def calculate_fee_money(
    *,
    currency: str,
    country_code: str,
    amount_net: Decimal,
    tax_class: TaxClass | None,
) -> Money:
    if not tax_class or not amount_net:
        return Money(
            currency=currency,
            net=Decimal(amount_net or 0),
            vat_rate=Decimal("0"),
            vat=Decimal("0"),
            gross=Decimal(amount_net or 0),
        )
    vat_rate = get_vat_rate(country_code=country_code, tax_class=tax_class)
    return money_from_net(currency=currency, unit_net=amount_net, vat_rate=vat_rate, qty=1)


def select_fee_rules(*, country_code: str, payment_method: str) -> list["FeeRule"]:
    from checkout.models import FeeRule

    country_code = (country_code or "").strip().upper()
    payment_method = (payment_method or "").strip()

    qs = FeeRule.objects.filter(is_active=True).order_by("sort_order", "code")
    if country_code:
        qs = qs.filter(models.Q(country_code="") | models.Q(country_code=country_code))
    if payment_method:
        qs = qs.filter(
            models.Q(payment_method_code="") | models.Q(payment_method_code=payment_method)
        )
    return list(qs)


def calculate_fees(
    *,
    currency: str,
    country_code: str,
    items_gross: Decimal,
    payment_method: str,
) -> list[tuple["FeeRule", Money]]:
    rules = select_fee_rules(country_code=country_code, payment_method=payment_method)
    out: list[tuple["FeeRule", Money]] = []
    items_gross_d = Decimal(items_gross or 0)

    for r in rules:
        if r.min_items_gross is not None and items_gross_d < Decimal(r.min_items_gross):
            continue
        if r.max_items_gross is not None and items_gross_d > Decimal(r.max_items_gross):
            continue

        m = calculate_fee_money(
            currency=currency,
            country_code=country_code,
            amount_net=Decimal(r.amount_net),
            tax_class=r.tax_class,
        )
        out.append((r, m))

    return out
