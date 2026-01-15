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


class NavigationItemOut(Schema):
    id: int
    label: str = ""
    href: str = ""
    link_type: str
    icon: str = ""
    image_src: str = ""
    badge: str = ""
    badge_kind: str = ""
    open_in_new_tab: bool = False
    children: list["NavigationItemOut"] = []


class NavigationOut(Schema):
    code: str
    items: list[NavigationItemOut] = []
