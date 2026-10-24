from __future__ import annotations

from decimal import Decimal

from ninja import Schema

from catalog.api_schemas import ProductImageOut, ProductListOut


class MoneyOut(Schema):
    currency: str = "EUR"
    net: Decimal
    vat_rate: Decimal
    vat: Decimal
    gross: Decimal


class DeliveryWindowOut(Schema):
    min_date: str
    max_date: str
    kind: str = "estimated"
    rule_code: str = ""
    source: str = ""


class CartItemOptionOut(Schema):
    option_type_code: str
    option_type_name: str
    option_value_code: str
    option_value_label: str


class CartItemOut(Schema):
    id: int
    variant_id: int
    offer_id: int | None = None
    sku: str
    name: str
    product_slug: str = ""
    # Varianto vardas/options — kad būtų aišku ką užsakė (ne tik produktas).
    variant_name: str = ""
    options: list[CartItemOptionOut] = []
    image: ProductImageOut | None = None
    qty: int
    stock_available: int
    unit_price: MoneyOut
    compare_at_price: MoneyOut | None = None
    discount_percent: int | None = None
    line_total: MoneyOut
    delivery_window: DeliveryWindowOut | None = None


class CartOut(Schema):
    currency: str = "EUR"
    country_code: str
    items: list[CartItemOut]
    items_total: MoneyOut
    delivery_window: DeliveryWindowOut | None = None


class CartItemAddIn(Schema):
    variant_id: int
    offer_id: int | None = None
    qty: int = 1


class CartItemUpdateIn(Schema):
    qty: int


class ShippingMethodOut(Schema):
    code: str
    name: str
    carrier_code: str = ""
    requires_pickup_point: bool = False
    image_url: str = ""
    pickup_points_url: str = ""
    price: MoneyOut


class FavoriteAddIn(Schema):
    variant_id: int | None = None
    # Kortelės heart'as — variantas sprendžiamas serveryje (pirmas aktyvus).
    product_id: int | None = None


class FavoriteVariantOut(Schema):
    id: int
    label: str
    sku: str
    in_stock: bool


class FavoriteItemOut(Schema):
    favorite_id: int
    variant_id: int
    product: "ProductListOut"
    variants: list[FavoriteVariantOut]


class FavoritesOut(Schema):
    items: list[FavoriteItemOut]
    variant_ids: list[int]
    count: int


class CheckoutPreviewIn(Schema):
    shipping_address_id: int
    shipping_method: str = "unisend_pickup"
    pickup_point_id: str | None = None
    payment_method: str = "bank_transfer"
    channel: str = "normal"
    coupon_code: str | None = None


class CheckoutPreviewOut(Schema):
    currency: str = "EUR"
    country_code: str
    shipping_method: str

    items: list[CartItemOut]

    delivery_window: DeliveryWindowOut | None = None

    items_total: MoneyOut
    discount_total: MoneyOut
    shipping_total: MoneyOut
    fees_total: MoneyOut
    fees: list["FeeOut"]
    order_total: MoneyOut


class FeeOut(Schema):
    code: str
    name: str
    amount: MoneyOut


class OrderConsentIn(Schema):
    kind: str
    document_version: str


class ConsentDefinitionOut(Schema):
    kind: str
    name: str
    document_version: str
    required: bool = True
    url: str = ""


class CheckoutConfirmIn(Schema):
    shipping_address_id: int
    shipping_method: str = "unisend_pickup"
    pickup_point_id: str | None = None
    payment_method: str = "bank_transfer"
    # Montonio: banko kodas (preferredProvider, pvz. "HABALT22")
    payment_provider: str | None = None
    # Montonio method: paymentInitiation|cardPayments|applePay|...
    montonio_method: str | None = None
    # Inbank: produkto kodas (keli finansavimo produktai vienoje parduotuvėje)
    inbank_product_code: str | None = None
    # Gift card: bearer code — debited fully at confirm (stored-value payment).
    gift_card_code: str | None = None
    channel: str = "normal"
    coupon_code: str | None = None
    consents: list[OrderConsentIn]


class CheckoutConfirmOut(Schema):
    order_id: int
    payment_provider: str
    payment_status: str
    redirect_url: str = ""
    payment_instructions: str = ""


class ApplyPickupPointIn(Schema):
    shipping_method: str
    pickup_point_id: str
    set_as_primary_pickup_point: bool = True


class ApplyPickupPointOut(Schema):
    shipping_address_id: int


class PaymentMethodOut(Schema):
    code: str
    name: str
    kind: str
    provider: str = ""
    instructions: str = ""
    logo_url: str = ""


class PaymentOptionOut(Schema):
    id: str
    kind: str
    title: str
    provider: str = ""
    instructions: str = ""
    logo_url: str = ""
    payload: dict = {}


class OrderLineOut(Schema):
    id: int
    sku: str
    name: str
    qty: int
    unit_price: MoneyOut
    compare_at_unit_price: MoneyOut | None = None
    discount_percent: int | None = None
    line_total: MoneyOut
    compare_at_line_total: MoneyOut | None = None
    delivery_window: DeliveryWindowOut | None = None
    # RMA info — FE rodo "Grąžinti" tik grąžinamoms eilutėms.
    returnable: bool = True
    return_days: int = 0
    returned_qty: int = 0


class OrderOut(Schema):
    id: int
    status: str
    status_label: str = ""
    delivery_status: str
    delivery_status_label: str = ""
    fulfillment_mode: str = ""
    fulfillment_mode_label: str = ""
    supplier_reservation_status: str = ""
    supplier_reservation_status_label: str = ""
    supplier_reserved_at: str = ""
    supplier_reference: str = ""
    currency: str
    country_code: str
    shipping_method: str
    carrier_code: str = ""
    tracking_number: str = ""
    delivered_at: str = ""
    invoice_number: str = ""

    payment_provider: str = ""
    payment_provider_label: str = ""
    payment_status: str = ""
    payment_status_label: str = ""
    payment_redirect_url: str = ""
    payment_instructions: str = ""

    items: list[OrderLineOut]

    delivery_window: DeliveryWindowOut | None = None

    items_total: MoneyOut
    discount_total: MoneyOut
    shipping_total: MoneyOut
    fees_total: MoneyOut
    fees: list[FeeOut]
    order_total: MoneyOut

    created_at: str
