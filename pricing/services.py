from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal, ROUND_HALF_UP

from django.db import models

from catalog.models import TaxClass, TaxRate


MONEY_PLACES = Decimal("0.01")


def quantize_money(amount: Decimal) -> Decimal:
    return amount.quantize(MONEY_PLACES, rounding=ROUND_HALF_UP)


@dataclass(frozen=True)
class VatBreakdown:
    unit_net: Decimal
    vat_rate: Decimal
    unit_vat: Decimal
    unit_gross: Decimal

    total_net: Decimal
    total_vat: Decimal
    total_gross: Decimal


def get_vat_rate(*, country_code: str, tax_class: TaxClass, at: date | None = None) -> Decimal:
    country_code = (country_code or "").strip().upper()
    if len(country_code) != 2:
        raise ValueError("Invalid country_code")

    at_date = at or date.today()

    qs = TaxRate.objects.filter(
        is_active=True,
        tax_class=tax_class,
        country_code=country_code,
        valid_from__lte=at_date,
    ).filter(
        # valid_to is open-ended or includes date
        models.Q(valid_to__isnull=True) | models.Q(valid_to__gte=at_date)
    )

    rate_obj = qs.order_by("-valid_from").first()
    if not rate_obj:
        raise LookupError("VAT rate not found")

    return Decimal(rate_obj.rate)


def compute_vat(*, unit_net: Decimal, vat_rate: Decimal, qty: int = 1) -> VatBreakdown:
    qty_i = int(qty)
    if qty_i <= 0:
        raise ValueError("qty must be positive")

    unit_net_q = quantize_money(Decimal(unit_net))
    vat_rate_d = Decimal(vat_rate)

    unit_vat = quantize_money(unit_net_q * vat_rate_d)
    unit_gross = quantize_money(unit_net_q + unit_vat)

    total_net = quantize_money(unit_net_q * qty_i)
    total_vat = quantize_money(unit_vat * qty_i)
    total_gross = quantize_money(unit_gross * qty_i)

    return VatBreakdown(
        unit_net=unit_net_q,
        vat_rate=vat_rate_d,
        unit_vat=unit_vat,
        unit_gross=unit_gross,
        total_net=total_net,
        total_vat=total_vat,
        total_gross=total_gross,
    )
