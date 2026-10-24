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


class CmsCategoryOut(Schema):
    slug: str
    name: str = ""
    description: str = ""
    icon: str = ""
    page_count: int = 0


class CmsPageListOut(Schema):
    slug: str
    title: str = ""
    updated_at: datetime


class StoreLocationOut(Schema):
    id: int
    name: str
    line1: str
    city: str
    postal_code: str = ""
    country_code: str
    phone: str = ""
    email: str = ""
    latitude: str | None = None
    longitude: str | None = None
    opening_hours: dict
    services: list[str]
    description: str = ""
    is_pickup_point: bool = False


class NavItemOut(Schema):
    id: int
    kind: str
    label: str = ""
    url: str = ""
    icon: str = ""
    badge: str = ""
    highlight: bool = False
    new_tab: bool = False
    show_in: str = "all"
    children: list["NavItemOut"] = []


NavItemOut.model_rebuild()


class MegaMenuFeatureOut(Schema):
    title: str = ""
    text: str = ""
    image_url: str = ""
    url: str = ""
    cta_label: str = ""


class NavigationOut(Schema):
    promo: NavItemOut | None = None
    items: list[NavItemOut]
    # key: root category id (str) or "default"
    mega_features: dict[str, MegaMenuFeatureOut] = {}
