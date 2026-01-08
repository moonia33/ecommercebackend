from __future__ import annotations

from decimal import Decimal

from ninja import Schema


class MoneyOut(Schema):
    currency: str = "EUR"
    net: Decimal
    vat_rate: Decimal
    vat: Decimal
    gross: Decimal


class CartItemOut(Schema):
    id: int
    variant_id: int
    sku: str
    name: str
    qty: int
    stock_available: int
    unit_price: MoneyOut
    line_total: MoneyOut


class CartOut(Schema):
    currency: str = "EUR"
    country_code: str
    items: list[CartItemOut]
    items_total: MoneyOut


class CartItemAddIn(Schema):
    variant_id: int
    qty: int = 1


class CartItemUpdateIn(Schema):
    qty: int


class ShippingMethodOut(Schema):
    code: str
    name: str
    carrier_code: str = ""
    requires_pickup_point: bool = False
    price: MoneyOut


class CheckoutPreviewIn(Schema):
    shipping_address_id: int
    shipping_method: str = "lpexpress"
    pickup_point_id: str | None = None


class CheckoutPreviewOut(Schema):
    currency: str = "EUR"
    country_code: str
    shipping_method: str

    items: list[CartItemOut]

    items_total: MoneyOut
    shipping_total: MoneyOut
    order_total: MoneyOut


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
    shipping_method: str = "lpexpress"
    pickup_point_id: str | None = None
    payment_method: str = "klix"
    consents: list[OrderConsentIn]


class CheckoutConfirmOut(Schema):
    order_id: int
    payment_provider: str
    payment_status: str
    redirect_url: str = ""
    payment_instructions: str = ""


class PaymentMethodOut(Schema):
    code: str
    name: str
    kind: str
    provider: str = ""
    instructions: str = ""


class OrderLineOut(Schema):
    id: int
    sku: str
    name: str
    qty: int
    unit_price: MoneyOut
    line_total: MoneyOut


class OrderOut(Schema):
    id: int
    status: str
    delivery_status: str
    currency: str
    country_code: str
    shipping_method: str
    carrier_code: str = ""
    tracking_number: str = ""

    payment_provider: str = ""
    payment_status: str = ""
    payment_redirect_url: str = ""
    payment_instructions: str = ""

    items: list[OrderLineOut]

    items_total: MoneyOut
    shipping_total: MoneyOut
    order_total: MoneyOut

    created_at: str
