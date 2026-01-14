from __future__ import annotations

from decimal import Decimal

from ninja import Schema


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


class CartItemOut(Schema):
    id: int
    variant_id: int
    offer_id: int | None = None
    sku: str
    name: str
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


class CheckoutPreviewIn(Schema):
    shipping_address_id: int
    shipping_method: str = "unisend_pickup"
    pickup_point_id: str | None = None
    payment_method: str = "klix"
    neopay_bank_bic: str | None = None
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
    payment_method: str = "klix"
    neopay_bank_bic: str | None = None
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

    payment_provider: str = ""
    payment_provider_label: str = ""
    payment_status: str = ""
    payment_status_label: str = ""
    payment_redirect_url: str = ""
    payment_instructions: str = ""

    neopay_bank_bic: str = ""
    neopay_bank_name: str = ""

    items: list[OrderLineOut]

    delivery_window: DeliveryWindowOut | None = None

    items_total: MoneyOut
    discount_total: MoneyOut
    shipping_total: MoneyOut
    fees_total: MoneyOut
    fees: list[FeeOut]
    order_total: MoneyOut

    created_at: str
