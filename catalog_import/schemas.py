from __future__ import annotations

from decimal import Decimal

from ninja import Schema


class ImportTranslationIn(Schema):
    name: str = ""
    slug: str = ""
    description: str = ""
    seo_title: str = ""
    seo_description: str = ""
    seo_keywords: str = ""


class ImportFeatureIn(Schema):
    code: str
    value: str
    # feature name translations: {"lt": "Spalva", "pl": "Kolor"}
    name_translations: dict[str, str] = {}
    # value translations: {"lt": "Raudona", "pl": "Czerwony"}
    value_translations: dict[str, str] = {}
    # Normalized numeric value(s) in the canonical unit (format_version=2+).
    value_num: Decimal | None = None
    value_num_max: Decimal | None = None
    unit: str = ""
    # text | number | range | enum | bool
    datatype: str = ""


class OptionValueMetaIn(Schema):
    """Numeric metadata for one option value (format_version=2+)."""

    num: Decimal | None = None
    num_max: Decimal | None = None
    unit: str = ""


class ImportImageIn(Schema):
    url: str
    alt: str = ""
    sort_order: int = 0


class ImportVideoIn(Schema):
    url: str
    # product | instructional | tutorial
    type: str = "product"
    language: str = ""
    sort_order: int = 0


class ImportVariantIn(Schema):
    code: str
    product_group_code: str = ""
    # Tikrasis tiekėjo art-no (be vardų-erdvės prefikso) – užsakymų administravimui.
    # `code` gali būti prefiksuotas ('korda-GRD198') dėl globalaus SKU unikalumo.
    supplier_code: str = ""
    ean: str = ""
    # Variant display name (e.g. supplier 'variantas' remainder); empty keeps existing.
    name: str = ""
    # option_type_code -> option_value code/label, e.g. {"size": "m"}
    options: dict[str, str] = {}
    # variant-level features (differ between variants, e.g. weight/length);
    # shared features stay on the product-level `features` list
    features: list[ImportFeatureIn] = []
    price_net: Decimal | None = None
    cost_net: Decimal | None = None
    weight_g: int = 0
    length_cm: int = 0
    width_cm: int = 0
    height_cm: int = 0
    is_active: bool = True
    # Pakuotės / trade-pack info (FOX): pack_unit='pcs', trade_pack_qty=dėžėje
    # vienetų, pack_barcode=dėžės EAN; sold_as_pack=True -> variantas = visa
    # dėžė (ean=dėžės EAN, kainos jau padaugintos iš trade_pack_qty).
    pack_unit: str = ""
    pack_quantity: int = 0
    trade_pack_qty: int = 0
    pack_barcode: str = ""
    sold_as_pack: bool = False
    pack_price: Decimal | None = None
    pack_purchase_price: Decimal | None = None
    # variant-specific image URLs (attached to this variant only)
    images: list[ImportImageIn] = []
    # warehouse_code -> qty
    stock: dict[str, int] = {}


class ImportProductIn(Schema):
    code: str
    product_group_code: str = ""
    ean: str = ""
    brand_code: str = ""
    # shop Category.slug (mapping happens in the normalizer)
    category: str = ""
    tax_class: str = "standard"
    is_active: bool = True
    price_net: Decimal
    cost_net: Decimal | None = None
    weight_g: int = 0
    length_cm: int = 0
    width_cm: int = 0
    height_cm: int = 0
    # "lt" required (base language); others become ProductTranslation rows
    translations: dict[str, ImportTranslationIn]
    features: list[ImportFeatureIn] = []
    # papildoma teisinė/saugos info: {"gpsr": "...", "ingredients": "..."}
    additional_info: dict[str, str] = {}
    # option value label'ai filtrams/UI: {"size": {"2xl": "2XL"}}
    option_labels: dict[str, dict[str, str]] = {}
    # option value numeric meta: {"casting-weight": {"10-40-g": {num,num_max,unit}}}
    option_value_meta: dict[str, dict[str, OptionValueMetaIn]] = {}
    images: list[ImportImageIn] = []
    videos: list[ImportVideoIn] = []
    # warehouse_code -> qty (product-level stock goes to the default variant)
    stock: dict[str, int] = {}
    variants: list[ImportVariantIn] = []


class CatalogImportEnvelope(Schema):
    """Envelope validated strictly; items stay raw and are validated per-row."""

    format_version: int = 1
    source: str = "normalizer"
    mode: str = "upsert"  # upsert | full_sync
    # upsert never deletes; full_sync + delete_missing=true deactivates absent items
    delete_missing: bool = False
    items: list[dict]


class CatalogImportFile(CatalogImportEnvelope):
    """Fully-typed document (docs/schema; items are validated per-row at runtime)."""

    items: list[ImportProductIn]


class StockImportItemIn(Schema):
    code: str  # variant sku
    qty: int


class StockImportEnvelope(Schema):
    format_version: int = 1
    source: str = "normalizer"
    warehouse_code: str = "main"
    items: list[dict]


class StockImportFile(StockImportEnvelope):
    items: list[StockImportItemIn]


class ImportRunOut(Schema):
    id: int
    kind: str
    source: str
    file_name: str
    status: str
    mode: str
    total_items: int
    ok_items: int
    failed_items: int
    skipped_items: int
    error_message: str
    created_at: str
    finished_at: str


class ImportItemOut(Schema):
    id: int
    row_no: int
    external_code: str
    status: str
    error: str
    product_id: int | None = None
    variant_id: int | None = None
