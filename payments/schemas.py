from __future__ import annotations

from ninja import Schema


class NeopayCallbackIn(Schema):
    token: str


class NeopayBankOut(Schema):
    country_code: str
    bic: str
    name: str
    service_types: list[str] = []
    logo_url: str = ""
    is_operating: bool = True


class NeopayCountryOut(Schema):
    code: str
    name: str
    currency: str = ""
    default_language: str = ""
    languages: list[str] = []
    rules: dict[str, str] = {}
    aspsps: list[NeopayBankOut] = []
