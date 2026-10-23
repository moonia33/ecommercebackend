from __future__ import annotations

from decimal import Decimal

from ninja import Schema


class MoneyOut(Schema):
    currency: str
    net: Decimal
    vat_rate: Decimal
    vat: Decimal
    gross: Decimal


class BrandCategoryOut(Schema):
    slug: str
    name: str
    product_count: int


class BrandOut(Schema):
    id: int
    slug: str
    name: str
    description: str = ""
    logo_url: str | None = None
    # 256px webp thumb (jei sugeneruotas; kitaip fallback i logo_url).
    logo_thumb_url: str | None = None
    logo_alt: str = ""
    seo_title: str = ""
    seo_description: str = ""
    seo_keywords: str = ""
    # Brand „shelf": kategorijos, kuriose brand'as turi prekių (detail endpoint).
    categories: list[BrandCategoryOut] = []


class BrandRefOut(Schema):
    id: int
    slug: str
    name: str
    # Lengvas ref'as; logo naudojama PDP brand sekcijoje.
    logo_url: str | None = None
    logo_thumb_url: str | None = None


class CategoryOut(Schema):
    id: int
    slug: str
    name: str
    parent_id: int | None = None
    # 'grid' (default) arba 'list' – storefront prekių sąrašo template.
    list_template: str = "grid"

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


class ImageSourceOut(Schema):
    url: str
    width: int


class ProductImageOut(Schema):
    avif_url: str | None = None
    webp_url: str | None = None
    url: str
    alt_text: str
    sort_order: int
    # srcset rinkiniai FE <source srcset>: [{url, width}]. Listing kontekstas
    # duoda kvadratines (300/600), PDP — proporcingas (300/800) versijas.
    avif_srcset: list[ImageSourceOut] = []
    webp_srcset: list[ImageSourceOut] = []
    # Originalas (zoom/lightbox PDP'je).
    full_url: str | None = None


class VariantOptionOut(Schema):
    option_type_code: str
    option_type_name: str
    option_value_code: str
    option_value_label: str


class VariantOut(Schema):
    id: int
    sku: str
    barcode: str | None = None
    product_group_code: str = ""
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
    # Trade-pack info (manufacturer data, same for all suppliers)
    sold_as_pack: bool = False
    pack_quantity: int = 0
    trade_pack_qty: int = 0
    pack_unit: str = ""
    pack_barcode: str = ""
    pack_price: MoneyOut | None = None
    options: list[VariantOptionOut]
    # Variant-level features (differ between variants; override product
    # features with the same feature_code on PDP merge)
    features: list["ProductFeatureOut"] = []
    images: list[ProductImageOut] = []


class ProductListOut(Schema):
    id: int
    sku: str
    slug: str
    name: str
    is_active: bool
    is_part: bool = False
    product_group_code: str = ""

    brand: BrandRefOut | None = None
    category: CategoryRefOut | None = None

    images: list[ProductImageOut]

    # Representative price for lists: min active variant net + VAT breakdown
    price: MoneyOut
    # "Price to" for variant ranges: max active variant list price when it
    # differs from `price`; null when all variants share one price.
    price_to: MoneyOut | None = None

    compare_at_price: MoneyOut | None = None
    discount_percent: int | None = None

    has_video: bool = False
    labels: list[str] = []


class ProductFeatureOut(Schema):
    feature_id: int
    feature_code: str
    feature_name: str
    value_id: int
    value: str
    value_label: str | None = None
    value_num: Decimal | None = None
    value_num_max: Decimal | None = None
    unit: str = ""
    datatype: str = ""


class DeliveryWindowOut(Schema):
    min_date: str
    max_date: str
    kind: str = "estimated"
    rule_code: str = ""
    source: str = ""


class ContentBlockOut(Schema):
    key: str
    title: str = ""
    placement: str
    type: str
    payload: dict


class ProductVideoOut(Schema):
    url: str
    video_type: str
    language_code: str = ""
    sort_order: int = 0


class ProductDetailOut(Schema):
    id: int
    sku: str
    slug: str
    name: str
    description: str
    is_active: bool
    product_group_code: str = ""

    seo_title: str = ""
    seo_description: str = ""
    seo_keywords: str = ""

    brand: BrandRefOut | None = None
    category: CategoryRefOut | None = None

    images: list[ProductImageOut]
    videos: list[ProductVideoOut] = []
    features: list[ProductFeatureOut] = []
    delivery_window: DeliveryWindowOut | None = None
    content_blocks: list[ContentBlockOut] = []
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
    label: str | None = None


class FeatureOut(Schema):
    id: int
    code: str
    name: str
    # 'checkbox' (default) renders values; 'range' renders a numeric
    # min/max slider and carries min/max/unit instead of values.
    widget: str = "checkbox"
    min: Decimal | None = None
    max: Decimal | None = None
    unit: str = ""
    values: list[FeatureValueOut] = []


class OptionValueOut(Schema):
    id: int
    code: str
    label: str
    value_num: Decimal | None = None
    value_num_max: Decimal | None = None
    unit: str = ""


class OptionTypeOut(Schema):
    id: int
    code: str
    name: str
    display_type: str = "radio"
    swatch_type: str | None = None
    # Same facet widget contract as FeatureOut (F4).
    widget: str = "checkbox"
    min: Decimal | None = None
    max: Decimal | None = None
    unit: str = ""
    values: list[OptionValueOut] = []


class ProductPageOut(Schema):
    items: list[ProductListOut]
    count: int


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
