from __future__ import annotations

from decimal import Decimal

from ninja import Schema


class ShippingCountryOut(Schema):
    code: str
    name: str


class ShippingMoneyOut(Schema):
    currency: str
    net: Decimal
    vat_rate: Decimal
    vat: Decimal
    gross: Decimal


class ShippingMethodPublicOut(Schema):
    """Viešas PDP pristatymo metodas (be auth — kaip /checkout/shipping-methods,
    bet be pickup_points_url/image_url — PDP rodo ikonas, ne logotipus)."""
    code: str
    name: str
    carrier_code: str = ""
    requires_pickup_point: bool = False
    price: ShippingMoneyOut
