from __future__ import annotations

from ninja import Schema


class LockerOut(Schema):
    id: str
    name: str = ""
    lockerType: str = ""

    countryCode: str = ""
    city: str = ""
    street: str = ""
    postalCode: str = ""

    latitude: float | None = None
    longitude: float | None = None


class StatusOut(Schema):
    raw: list[dict]
