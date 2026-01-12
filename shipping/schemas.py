from __future__ import annotations

from ninja import Schema


class ShippingCountryOut(Schema):
    code: str
    name: str
