from __future__ import annotations

from decimal import Decimal

from ninja import Schema


class QuoteIn(Schema):
    variant_id: int
    country_code: str
    qty: int = 1


class QuoteOut(Schema):
    variant_id: int
    country_code: str

    unit_net: Decimal
    vat_rate: Decimal
    unit_vat: Decimal
    unit_gross: Decimal

    qty: int
    total_net: Decimal
    total_vat: Decimal
    total_gross: Decimal
