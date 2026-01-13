from __future__ import annotations

from ninja import Schema


class TerminalOut(Schema):
    id: str
    name: str = ""

    countryCode: str = ""
    city: str = ""
    locality: str = ""
    street: str = ""
    postalCode: str = ""

    latitude: float | None = None
    longitude: float | None = None
