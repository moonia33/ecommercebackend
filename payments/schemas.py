from __future__ import annotations

from ninja import Schema


class NeopayCallbackIn(Schema):
    token: str


class NeopayBankOut(Schema):
    country_code: str
    bic: str
    name: str
    service_types: list[str] = []
