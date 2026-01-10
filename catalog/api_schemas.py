from __future__ import annotations

from decimal import Decimal

from ninja import Schema


class MoneyOut(Schema):
    currency: str
    net: Decimal
    vat_rate: Decimal
    vat: Decimal
    gross: Decimal


class BrandOut(Schema):
    id: int
    slug: str
    name: str


class BrandRefOut(Schema):
    id: int
    slug: str
    name: str


class CategoryOut(Schema):
    id: int
    slug: str
    name: str
    parent_id: int | None = None

    description: str = ""
    hero_image_url: str | None = None
    menu_icon_url: str | None = None

    seo_title: str = ""
    seo_description: str = ""
    seo_keywords: str = ""


class CategoryRefOut(Schema):
    id: int
    slug: str
    name: str


class ProductImageOut(Schema):
    avif_url: str | None = None
    webp_url: str | None = None
    url: str
    alt_text: str
    sort_order: int


class VariantOptionOut(Schema):
    option_type_code: str
    option_type_name: str
    option_value_code: str
    option_value_label: str


class VariantOut(Schema):
    id: int
    sku: str
    barcode: str
    name: str
    is_active: bool
    stock_available: int
    price: MoneyOut
    compare_at_price: MoneyOut | None = None
    offer_id: int | None = None
    offer_label: str = ""
    condition_grade: str = ""
    offer_visibility: str = ""
    discount_percent: int | None = None
    options: list[VariantOptionOut]


class ProductListOut(Schema):
    id: int
    sku: str
    slug: str
    name: str
    is_active: bool

    brand: BrandRefOut | None = None
    category: CategoryRefOut | None = None

    images: list[ProductImageOut]

    # Representative price for lists: min active variant net + VAT breakdown
    price: MoneyOut

    compare_at_price: MoneyOut | None = None
    discount_percent: int | None = None


class ProductFeatureOut(Schema):
    feature_id: int
    feature_code: str
    feature_name: str
    value_id: int
    value: str


class ProductDetailOut(Schema):
    id: int
    sku: str
    slug: str
    name: str
    description: str
    is_active: bool

    seo_title: str = ""
    seo_description: str = ""
    seo_keywords: str = ""

    brand: BrandRefOut | None = None
    category: CategoryRefOut | None = None

    images: list[ProductImageOut]
    features: list[ProductFeatureOut] = []
    variants: list[VariantOut]


class CategoryDetailOut(CategoryOut):
    pass


class ProductGroupOut(Schema):
    id: int
    code: str
    name: str
    description: str = ""


class FeatureValueOut(Schema):
    id: int
    value: str


class FeatureOut(Schema):
    id: int
    code: str
    name: str
    values: list[FeatureValueOut] = []


class OptionValueOut(Schema):
    id: int
    code: str
    label: str


class OptionTypeOut(Schema):
    id: int
    code: str
    name: str
    display_type: str = "radio"
    swatch_type: str | None = None
    values: list[OptionValueOut] = []


class CatalogFacetsOut(Schema):
    categories: list[CategoryOut] = []
    brands: list[BrandOut] = []
    product_groups: list[ProductGroupOut] = []
    features: list[FeatureOut] = []
    option_types: list[OptionTypeOut] = []


class BackInStockSubscribeIn(Schema):
    email: str
    product_id: int | None = None
    variant_id: int | None = None
    channel: str = "normal"


class BackInStockSubscribeOut(Schema):
    status: str
