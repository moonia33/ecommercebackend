from __future__ import annotations

from datetime import datetime

from ninja import Schema


class ContentBlockOut(Schema):
    type: str
    payload: dict


class CmsPageOut(Schema):
    slug: str
    title: str = ""

    seo_title: str = ""
    seo_description: str = ""

    updated_at: datetime

    content_blocks: list[ContentBlockOut]
