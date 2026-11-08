from __future__ import annotations

from datetime import datetime

from ninja import Schema

from legal.schemas import LegalInfoOut


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

    legal: LegalInfoOut | None = None


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
    # Footer columns: roots = column headers, children = links.
    footers: list[NavItemOut] = []
    # Legal/bottom strip links under the footer columns.
    bottom_links: list[NavItemOut] = []
    # key: root category id (str) or "default"
    mega_features: dict[str, MegaMenuFeatureOut] = {}


class BlogCategoryOut(Schema):
    slug: str
    name: str = ""
    description: str = ""
    post_count: int = 0


class BlogAlternateOut(Schema):
    language_code: str
    slug: str


class BlogPostListOut(Schema):
    id: int
    slug: str
    language_code: str
    title: str
    excerpt: str = ""
    cover_image: str = ""
    cover_image_alt: str = ""
    published_at: datetime | None = None
    reading_minutes: int = 0
    author_name: str = ""
    categories: list[BlogCategoryOut] = []
    is_featured: bool = False


class BlogPostPageOut(Schema):
    page: int
    page_size: int
    total_count: int
    results: list[BlogPostListOut]


class BlogPostOut(BlogPostListOut):
    seo_title: str = ""
    seo_description: str = ""
    updated_at: datetime
    content_blocks: list[ContentBlockOut] = []
    alternates: list[BlogAlternateOut] = []
    related_products: list[dict] = []
    redirect_to_slug: str = ""


class BlogSitemapEntryOut(Schema):
    language_code: str
    slug: str
    updated_at: datetime
