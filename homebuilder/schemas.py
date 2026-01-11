from __future__ import annotations

from datetime import datetime

from ninja import Schema


class HomeSectionOut(Schema):
    type: str
    payload: dict
    items: list[dict] | None = None


class HomeOut(Schema):
    code: str
    title: str = ""

    seo_title: str = ""
    seo_description: str = ""

    updated_at: datetime

    sections: list[HomeSectionOut]
