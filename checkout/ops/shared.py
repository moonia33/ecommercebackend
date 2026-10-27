from __future__ import annotations

from datetime import date
from decimal import Decimal

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import Count, F, Max, Prefetch, Q, Sum
from django.utils import timezone
from ninja import Query, Router, Schema
from ninja.errors import HttpError

from accounts.auth import JWTAuth
from api.i18n import get_default_language_code, get_supported_languages
from catalog.models import (
    Brand,
    Category,
    Feature,
    FeatureValue,
    InventoryAdjustment,
    InventoryItem,
    OptionType,
    OptionValue,
    Product,
    ProductFeatureValue,
    ProductGroup,
    ProductOptionType,
    ProductTranslation,
    TaxClass,
    Variant,
    VariantOptionValue,
    Warehouse,
)
from checkout.models import (
    InventoryAllocation,
    OpsJob,
    Order,
    OrderDiscount,
    OrderEvent,
    OrderLine,
    PaymentIntent,
    Shipment,
    Surcharge,
)
from checkout.services import (
    capture_inventory_for_order, finalize_paid_order, release_inventory_for_order)
from promotions.services import (
    redeem_coupon_for_paid_order,
    release_coupon_for_order,
    reserve_coupon_for_order,
)


router = Router(tags=["ops"])
_auth = JWTAuth()


class OpsOrderSearchIn(Schema):
    shop_code: str = ""
    status: str = ""
    payment_status: str = ""
    shipping_type: str = ""
    assignee: str = ""
    date_from: date | None = None
    date_to: date | None = None
    q: str = ""
    product_q: str = ""
    supplier_code: str = ""
    supplier_status: str = ""
    manufacturer_q: str = ""
    remote_only: bool = False
    eta_from: date | None = None
    eta_to: date | None = None
    page: int = 1
    page_size: int = 50


class OpsOrderBulkActionIn(Schema):
    action: str
    selected_ids: list[int] = []
    assignee: str = ""
    note: str = ""


class OpsOrderActionIn(Schema):
    assignee: str = ""
    note: str = ""


class OpsShipmentBulkLabelIn(Schema):
    order_ids: list[int]


class OpsInventoryAdjustIn(Schema):
    delta: int
    reason_code: str
    comment: str = ""
    # Remote (tiekėjo) sandėliams privalomas sąmoningas patvirtinimas —
    # rankinis korekcija nuotolinėje pusėje gali sulaužyti offer'io
    # atsargas ir pirkėjo matomą prieinamumą.
    confirm_remote: bool = False


class OpsVariantPriceUpdateIn(Schema):
    price_eur: Decimal


class OpsOfferVisibilityIn(Schema):
    visibility: str


class OpsCatalogBulkActionIn(Schema):
    action: str
    product_ids: list[int] = []
    # `assign_category` veiksmui — paskirties Category.id
    category_id: int | None = None


class OpsPaymentActionIn(Schema):
    note: str = ""


class OpsShopOut(Schema):
    id: int | None = None
    code: str = ""
    name: str = ""
    primary_domain: str = ""


class OpsCustomerOut(Schema):
    user_id: int | None = None
    email: str = ""
    full_name: str = ""
    phone: str = ""
    company: str = ""
    is_guest: bool = False


class OpsOrderMatchLineOut(Schema):
    order_line_id: int
    variant_id: int | None = None
    variant_sku: str = ""
    barcode: str = ""
    sku: str
    name: str
    qty: int
    product_id: int | None = None
    product_name: str = ""
    product_slug: str = ""
    thumbnail_url: str = ""
    warehouse_code: str = ""
    warehouse_name: str = ""
    warehouse_country: str = ""
    warehouse_kind: str = ""
    allocated_qty: int = 0
    unallocated_qty: int = 0
    returnable: bool = True
    return_days: int = 0
    returned_qty: int = 0
    return_deadline: str = ""
    brand_name: str = ""
    unit_net: str = "0"
    unit_vat: str = "0"
    unit_gross: str = "0"
    line_total_gross: str = "0"


class OpsOrderListItemOut(Schema):
    id: int
    shop: OpsShopOut
    customer: OpsCustomerOut
    status: str
    payment_status: str = ""
    delivery_status: str
    fulfillment_mode: str
    supplier_reservation_status: str
    supplier_reference: str = ""
    country_code: str
    shipping_method: str
    shipping_country_code: str = ""
    delivery_min_date: str = ""
    delivery_max_date: str = ""
    created_at: str
    amount_gross: str = "0"
    amount_currency: str = "EUR"
    matched_items_count: int
    matched_suppliers: list[str]
    matched_lines: list[OpsOrderMatchLineOut]


class OpsOrderSearchOut(Schema):
    page: int
    page_size: int
    total_count: int
    results: list[OpsOrderListItemOut]


class OpsActionResultOut(Schema):
    ok: bool
    message: str = ""
    updated_ids: list[int] = []
    failed_ids: list[int] = []


class OpsShipmentOut(Schema):
    order_id: int
    delivery_status: str
    shipping_method: str
    carrier_code: str = ""
    carrier_shipment_id: str = ""
    tracking_number: str = ""
    label_generated_at: str = ""


class OpsShipmentListOut(Schema):
    page: int
    page_size: int
    total_count: int
    results: list[OpsShipmentOut]


class OpsShipmentRowOut(Schema):
    id: int
    order_id: int
    kind: str
    status: str
    shipping_method: str = ""
    carrier_code: str = ""
    carrier_shipment_id: str = ""
    tracking_number: str = ""
    pickup_locker_id: int | None = None
    pickup_point_id: str = ""
    pickup_point_name: str = ""
    label_url: str = ""
    label_generated_at: str = ""
    notes: str = ""
    created_at: str = ""
    updated_at: str = ""


class OpsShipmentRegistryOut(Schema):
    page: int
    page_size: int
    total_count: int
    results: list[OpsShipmentRowOut]


class OpsShipmentCreateIn(Schema):
    kind: str = "outbound"
    shipping_method: str = ""
    carrier_code: str = ""
    tracking_number: str = ""
    pickup_locker_id: int | None = None
    notes: str = ""


class OpsShipmentUpdateIn(Schema):
    kind: str | None = None
    status: str | None = None
    shipping_method: str | None = None
    carrier_code: str | None = None
    tracking_number: str | None = None
    carrier_shipment_id: str | None = None
    pickup_locker_id: int | None = None
    notes: str | None = None


class OpsOrderShippingUpdateIn(Schema):
    shipping_method: str | None = None
    pickup_locker_id: int | None = None


class OpsOrderShippingUpdateOut(Schema):
    ok: bool
    message: str = ""
    order_id: int = 0
    shipping_method: str = ""
    shipping_gross: str = ""
    price_delta_gross: str = ""
    pickup_point_name: str = ""
    new_shipment_id: int | None = None
    balance_due: str = ""


class OpsOrderSupplierUpdateIn(Schema):
    supplier_reservation_status: str | None = None
    supplier_reference: str | None = None
    supplier_notes: str | None = None


class OpsOrderAddressUpdateIn(Schema):
    user_address_id: int | None = None
    full_name: str = ""
    company: str = ""
    line1: str = ""
    city: str = ""
    postal_code: str = ""
    country_code: str = ""
    phone: str = ""
    save_to_customer: bool = False
    set_default_shipping: bool = False


class OpsOrderLineIn(Schema):
    variant_id: int
    qty: int = 1
    offer_id: int | None = None


class OpsOrderLineUpdateIn(Schema):
    qty: int | None = None
    unit_gross: str | None = None
    unit_net: str | None = None
    name: str | None = None


class OpsOrderDiscountIn(Schema):
    # percent | amount (gross EUR) | coupon (existing coupon code)
    kind: str
    value: str | None = None
    code: str = ""
    name: str = ""


class OpsOrderDiscountOut(Schema):
    id: int
    order_id: int
    kind: str
    code: str = ""
    name: str = ""
    net: str = "0"
    vat: str = "0"
    gross: str = "0"


class OpsSurchargeCollectIn(Schema):
    method: str = "montonio"
    send_email: bool = True
    language_code: str | None = None


class OpsSurchargeIn(Schema):
    amount_gross: str
    reason: str = ""
    method: str = "montonio"
    send_email: bool = True
    language_code: str | None = None


class OpsSurchargeOut(Schema):
    id: int
    order_id: int
    direction: str = "charge"
    source: str = "manual"
    amount_gross: str
    currency: str
    method: str = ""
    status: str
    reason: str = ""
    payment_url: str = ""
    emailed_at: str = ""
    paid_at: str = ""
    created_at: str = ""
    refund_in_flight: bool = False


class OpsOrderCreateLineIn(Schema):
    variant_id: int
    qty: int = 1


class OpsOrderCreateIn(Schema):
    user_email: str = ""
    user_id: int | None = None
    shipping_method: str = "dpd_locker"
    pickup_locker_id: int | None = None
    shipping_full_name: str = ""
    shipping_phone: str = ""
    shipping_line1: str = ""
    shipping_city: str = ""
    shipping_postal_code: str = ""
    shipping_country_code: str = "LT"
    country_code: str = "LT"
    shipping_net_manual: str | None = None
    status: str = "pending_payment"
    note: str = ""
    lines: list[OpsOrderCreateLineIn] = []


class OpsOrderMergeIn(Schema):
    primary_order_id: int
    merged_order_ids: list[int] = []


class OpsLockerOptionOut(Schema):
    id: int
    name: str
    country_code: str = ""
    city: str = ""
    address: str = ""


class OpsPaymentOut(Schema):
    id: int
    order_id: int
    provider: str
    status: str
    amount_gross: str
    currency: str
    external_id: str = ""
    created_at: str
    updated_at: str


class OpsPaymentListOut(Schema):
    page: int
    page_size: int
    total_count: int
    results: list[OpsPaymentOut]


class OpsInventoryItemOut(Schema):
    id: int
    sku: str
    product_id: int | None = None
    product_name: str
    thumbnail_url: str = ""
    warehouse_code: str
    warehouse_kind: str = ""
    qty_on_hand: int
    qty_reserved: int
    qty_available: int
    offer_visibility: str


class OpsInventoryListOut(Schema):
    page: int
    page_size: int
    total_count: int
    results: list[OpsInventoryItemOut]


class OpsCatalogProductOut(Schema):
    id: int
    sku: str
    name: str
    slug: str
    is_active: bool
    brand_name: str = ""
    category_id: int | None = None
    category_name: str = ""
    variants_count: int
    thumbnail_url: str = ""


class OpsCatalogProductsOut(Schema):
    page: int
    page_size: int
    total_count: int
    results: list[OpsCatalogProductOut]


class OpsAuditEventOut(Schema):
    id: int
    order_id: int
    kind: str
    name: str
    actor_label: str
    payload: dict
    created_at: str


class OpsAuditEventsOut(Schema):
    page: int
    page_size: int
    total_count: int
    results: list[OpsAuditEventOut]


class OpsJobOut(Schema):
    id: int
    job_type: str
    status: str
    shop_id: int | None = None
    actor_user_id: int | None = None
    request_payload: dict
    result_payload: dict
    error_message: str = ""
    started_at: str = ""
    completed_at: str = ""
    created_at: str
    updated_at: str


class OpsJobsListOut(Schema):
    page: int
    page_size: int
    total_count: int
    results: list[OpsJobOut]


class OpsOrderAddressOut(Schema):
    full_name: str = ""
    company: str = ""
    line1: str = ""
    city: str = ""
    postal_code: str = ""
    country_code: str = ""
    phone: str = ""


class OpsPickupPointOut(Schema):
    pickup_point_id: str = ""
    pickup_point_name: str = ""
    country_code: str = ""
    raw: dict = {}


class OpsOrderTotalsOut(Schema):
    items_net: str = "0"
    items_vat: str = "0"
    items_gross: str = "0"
    shipping_net: str = "0"
    shipping_vat: str = "0"
    shipping_gross: str = "0"
    discounts_net: str = "0"
    discounts_vat: str = "0"
    discounts_gross: str = "0"
    total_net: str = "0"
    total_vat: str = "0"
    total_gross: str = "0"
    currency: str = "EUR"
    paid_gross: str = "0"
    balance_due: str = "0"


class OpsCustomerStatsOut(Schema):
    orders_count: int = 0
    paid_orders_count: int = 0
    total_spent: str = "0"
    last_order_at: str = ""


class OpsOrderDetailOut(OpsOrderListItemOut):
    totals: OpsOrderTotalsOut
    shipping_address: OpsOrderAddressOut
    pickup_point: OpsPickupPointOut | None = None
    tracking_number: str = ""
    carrier_code: str = ""
    carrier_shipment_id: str = ""
    label_pdf_url: str = ""
    label_generated_at: str = ""
    delivered_at: str = ""
    updated_at: str = ""
    supplier_notes: str = ""
    events: list[OpsAuditEventOut] = []
    payments: list[OpsPaymentOut] = []
    customer_stats: OpsCustomerStatsOut | None = None
    shipments: list[OpsShipmentRowOut] = []
    surcharges: list[OpsSurchargeOut] = []
    discounts: list[OpsOrderDiscountOut] = []


class OpsCustomerPhoneOut(Schema):
    phone: str
    label: str = ""
    is_primary: bool = False
    is_verified: bool = False


class OpsCustomerAddressOut(Schema):
    id: int
    label: str = ""
    full_name: str = ""
    company: str = ""
    company_reg_no: str = ""
    company_vat_no: str = ""
    line1: str = ""
    city: str = ""
    postal_code: str = ""
    country_code: str = ""
    phone: str = ""
    is_default_shipping: bool = False
    is_default_billing: bool = False


class OpsCustomerOrderOut(Schema):
    id: int
    status: str
    payment_status: str = ""
    delivery_status: str = ""
    created_at: str
    amount_gross: str = "0"
    currency: str = "EUR"
    items_count: int = 0


class OpsCustomerListItemOut(Schema):
    id: int
    email: str
    first_name: str = ""
    last_name: str = ""
    full_name: str = ""
    phone: str = ""
    is_active: bool = True
    is_staff: bool = False
    date_joined: str = ""
    last_login: str = ""
    orders_count: int = 0
    paid_orders_count: int = 0
    total_spent: str = "0"
    last_order_at: str = ""
    groups: list[str] = []


class OpsCustomerListOut(Schema):
    page: int
    page_size: int
    total_count: int
    results: list[OpsCustomerListItemOut]


class OpsCustomerDetailOut(OpsCustomerListItemOut):
    phones: list[OpsCustomerPhoneOut] = []
    addresses: list[OpsCustomerAddressOut] = []
    pickup_point: OpsPickupPointOut | None = None
    orders: list[OpsCustomerOrderOut] = []


class OpsCatalogVariantOptionOut(Schema):
    option_type_id: int
    option_type_code: str
    option_type_name: str
    option_value_id: int | None = None
    label: str = ""


class OpsVariantPickerOut(Schema):
    variant_id: int
    sku: str
    barcode: str = ""
    name: str = ""
    product_id: int | None = None
    product_name: str = ""
    product_slug: str = ""
    thumbnail_url: str = ""
    is_active: bool = True
    unit_net: str = "0"
    unit_gross: str = "0"
    stock_available: int = 0


class OpsCatalogVariantOut(Schema):
    id: int
    sku: str
    barcode: str = ""
    name: str = ""
    price_eur: str = "0"
    cost_eur: str = ""
    is_active: bool = True
    weight_g: int = 0
    length_cm: int = 0
    width_cm: int = 0
    height_cm: int = 0
    options: list[OpsCatalogVariantOptionOut] = []


class OpsCatalogOfferOut(Schema):
    inventory_item_id: int
    variant_id: int | None = None
    variant_sku: str = ""
    warehouse_code: str
    warehouse_name: str = ""
    qty_on_hand: int = 0
    qty_reserved: int = 0
    qty_available: int = 0
    offer_visibility: str = ""
    offer_price_override_eur: str = ""


class OpsProductTranslationOut(Schema):
    language_code: str
    name: str = ""
    slug: str = ""
    description: str = ""
    seo_title: str = ""
    seo_description: str = ""
    seo_keywords: str = ""


class OpsProductFeatureOut(Schema):
    id: int
    feature_id: int
    feature_code: str
    feature_name: str
    feature_value_id: int
    value: str


class OpsProductImageOut(Schema):
    id: int
    url: str = ""
    alt_text: str = ""
    sort_order: int = 0
    variant_skus: list[str] = []


class OpsProductVideoOut(Schema):
    id: int
    url: str
    video_type: str = "product"
    language_code: str = ""
    sort_order: int = 0


class OpsCatalogProductDetailOut(OpsCatalogProductOut):
    description: str = ""
    thumbnail_url: str = ""
    seo_title: str = ""
    seo_description: str = ""
    seo_keywords: str = ""
    additional_info: dict = {}
    brand_id: int | None = None
    category_id: int | None = None
    group_id: int | None = None
    tax_class_id: int | None = None
    option_type_ids: list[int] = []
    translations: list[OpsProductTranslationOut] = []
    features: list[OpsProductFeatureOut] = []
    images: list[OpsProductImageOut] = []
    videos: list[OpsProductVideoOut] = []
    variants: list[OpsCatalogVariantOut] = []
    offers: list[OpsCatalogOfferOut] = []


class OpsLanguageOut(Schema):
    code: str
    name: str


class OpsLookupOptionOut(Schema):
    id: int
    name: str
    code: str = ""
    depth: int = 0


class OpsFeatureValueOptionOut(Schema):
    id: int
    value: str


class OpsFeatureOptionOut(Schema):
    id: int
    code: str
    name: str
    allows_multiple: bool = False
    is_filterable: bool = True
    translations: dict[str, str] = {}
    values: list[OpsFeatureValueOptionOut] = []


class OpsOptionValueOptionOut(Schema):
    id: int
    code: str
    label: str
    translations: dict[str, str] = {}


class OpsOptionTypeOptionOut(Schema):
    id: int
    code: str
    name: str
    translations: dict[str, str] = {}
    values: list[OpsOptionValueOptionOut] = []


class OpsCatalogLookupsOut(Schema):
    default_language: str = "lt"
    languages: list[OpsLanguageOut] = []
    brands: list[OpsLookupOptionOut] = []
    categories: list[OpsLookupOptionOut] = []
    groups: list[OpsLookupOptionOut] = []
    tax_classes: list[OpsLookupOptionOut] = []
    features: list[OpsFeatureOptionOut] = []
    option_types: list[OpsOptionTypeOptionOut] = []


class OpsProductTranslationIn(Schema):
    language_code: str
    name: str = ""
    slug: str = ""
    description: str = ""
    seo_title: str = ""
    seo_description: str = ""
    seo_keywords: str = ""


class OpsProductUpdateIn(Schema):
    name: str | None = None
    slug: str | None = None
    description: str | None = None
    seo_title: str | None = None
    seo_description: str | None = None
    seo_keywords: str | None = None
    is_active: bool | None = None
    brand_id: int | None = None
    category_id: int | None = None
    group_id: int | None = None
    tax_class_id: int | None = None
    additional_info: dict | None = None
    translations: list[OpsProductTranslationIn] | None = None
    feature_value_ids: list[int] | None = None


class OpsVariantOptionIn(Schema):
    option_type_id: int
    option_value_id: int | None = None


class OpsVariantUpdateIn(Schema):
    name: str | None = None
    barcode: str | None = None
    price_eur: Decimal | None = None
    cost_eur: Decimal | None = None
    is_active: bool | None = None
    weight_g: int | None = None
    length_cm: int | None = None
    width_cm: int | None = None
    height_cm: int | None = None
    options: list[OpsVariantOptionIn] | None = None


class OpsFeatureValueCreateIn(Schema):
    value: str


def _ensure_shop_context_if_strict(request) -> None:
    if not getattr(settings, "SHOP_CONTEXT_ENABLED", False):
        return
    if not getattr(settings, "SHOP_CONTEXT_STRICT", False):
        return
    if getattr(request, "shop", None) is None:
        raise HttpError(404, "Shop not resolved for host")


def _require_ops_user(request):
    user = getattr(request, "auth", None)
    if user is None or not getattr(user, "is_authenticated", False):
        raise HttpError(401, "Unauthorized")
    if not (getattr(user, "is_staff", False) or getattr(user, "is_superuser", False)):
        raise HttpError(403, "Ops access requires staff permissions")
    return user


def _request_shop_id(request) -> int | None:
    shop = getattr(request, "shop", None)
    if shop is None:
        return None
    return int(shop.id)


def _to_iso(v) -> str:
    if not v:
        return ""
    try:
        return v.isoformat()
    except Exception:
        return ""


def _to_money_str(v) -> str:
    if v is None:
        return "0"
    try:
        return str(v)
    except Exception:
        return "0"


def _customer_full_name(user, fallback: str = "") -> str:
    if user is None:
        return fallback or ""
    first = (getattr(user, "first_name", "") or "").strip()
    last = (getattr(user, "last_name", "") or "").strip()
    full = " ".join([x for x in [first, last] if x]).strip()
    if full:
        return full
    return fallback or (getattr(user, "email", "") or "")


def _product_thumbnail_url(product) -> str:
    if product is None:
        return ""
    try:
        images = list(getattr(product, "images", []).all()[:1])
    except Exception:
        images = []
    if not images:
        return ""
    img = images[0]
    for candidate in [
        getattr(img, "listing_webp_url", ""),
        getattr(img, "listing_avif_url", ""),
        getattr(img, "url", ""),
    ]:
        c = (candidate or "").strip()
        if c:
            return c
    return ""


def _effective_order_total_gross(order: Order) -> Decimal:
    try:
        current = Decimal(getattr(order, "total_gross", 0) or 0)
    except Exception:
        current = Decimal("0")
    if current > 0:
        return current
    try:
        agg = order.lines.aggregate(sum_gross=Sum("total_gross"))
        summed = agg.get("sum_gross")
        return Decimal(summed or 0)
    except Exception:
        return current


def _track_order_event(*, order: Order, user, kind: str, name: str, payload: dict | None = None) -> None:
    OrderEvent.objects.create(
        order=order,
        kind=kind,
        name=name,
        actor_user=user if user and getattr(user, "is_authenticated", False) else None,
        actor_label=str(getattr(user, "email", "") or getattr(user, "username", "") or ""),
        payload=payload or {},
    )


def _get_order_for_ops(order_id: int, *, shop_id: int | None) -> Order:
    qs = Order.objects.select_related("payment_intent")
    if shop_id is not None:
        qs = qs.filter(shop_id=shop_id)
    else:
        qs = qs.filter(shop__isnull=True)
    order = qs.filter(id=order_id).first()
    if order is None:
        raise HttpError(404, "Order not found")
    return order


def _build_line_filter(filters: OpsOrderSearchIn, *, prefix: str = "") -> Q:
    p = f"{prefix}__" if prefix else ""
    line_q = Q()

    product_q = (filters.product_q or "").strip()
    if product_q:
        line_q &= (
            Q(**{f"{p}sku__icontains": product_q})
            | Q(**{f"{p}name__icontains": product_q})
            | Q(**{f"{p}variant__sku__icontains": product_q})
            | Q(**{f"{p}variant__barcode__icontains": product_q})
            | Q(**{f"{p}variant__product__name__icontains": product_q})
            | Q(**{f"{p}variant__product__slug__icontains": product_q})
        )

    supplier_code = (filters.supplier_code or "").strip()
    if supplier_code:
        line_q &= (
            Q(**{f"{p}offer__warehouse__code__iexact": supplier_code})
            | Q(**{f"{p}inventory_allocations__inventory_item__warehouse__code__iexact": supplier_code},
                **{f"{p}inventory_allocations__status__in": [
                    InventoryAllocation.Status.RESERVED,
                    InventoryAllocation.Status.CAPTURED,
                ]})
        )

    manufacturer_q = (filters.manufacturer_q or "").strip()
    if manufacturer_q:
        line_q &= (
            Q(**{f"{p}variant__product__brand__name__icontains": manufacturer_q})
            | Q(**{f"{p}variant__product__brand__slug__icontains": manufacturer_q})
        )

    return line_q


def _build_order_filter(filters: OpsOrderSearchIn, *, shop_id: int | None) -> tuple[Q, Q]:
    order_q = Q()
    line_q = _build_line_filter(filters)

    shop_code = (filters.shop_code or "").strip().lower()
    if shop_code:
        order_q &= Q(shop__code=shop_code)

    status = (filters.status or "").strip()
    if status:
        order_q &= Q(status=status)

    payment_status = (filters.payment_status or "").strip()
    if payment_status:
        order_q &= Q(payment_intent__status=payment_status)

    shipping_type = (filters.shipping_type or "").strip().lower()
    if shipping_type == "pickup":
        order_q &= Q(shipping_method__icontains="pickup")
    elif shipping_type == "courier":
        order_q &= ~Q(shipping_method__icontains="pickup")

    supplier_status = (filters.supplier_status or "").strip()
    if supplier_status:
        order_q &= Q(supplier_reservation_status__in=[
            s.strip() for s in supplier_status.split(",") if s.strip()])

    q = (filters.q or "").strip()
    if q:
        order_q &= (
            Q(tracking_number__icontains=q)
            | Q(carrier_shipment_id__icontains=q)
            | Q(supplier_reference__icontains=q)
            | Q(shipping_full_name__icontains=q)
            | Q(shipping_phone__icontains=q)
        )

    if filters.date_from:
        order_q &= Q(created_at__date__gte=filters.date_from)
    if filters.date_to:
        order_q &= Q(created_at__date__lte=filters.date_to)

    if filters.remote_only:
        order_q &= Q(
            fulfillment_mode__in=[
                Order.FulfillmentMode.DROPSHIP,
                Order.FulfillmentMode.REMOTE,
                Order.FulfillmentMode.MIXED,
            ]
        )

    if filters.eta_from:
        order_q &= Q(delivery_max_date__gte=filters.eta_from)
    if filters.eta_to:
        order_q &= Q(delivery_min_date__lte=filters.eta_to)

    if shop_id is not None:
        order_q &= Q(shop_id=shop_id)
    else:
        order_q &= Q(shop__isnull=True)

    return order_q, line_q


def _serialize_ops_order(order: Order) -> OpsOrderListItemOut:
    from checkout.returns import (
        line_is_returnable,
        line_return_days,
        returned_qty_map,
    )

    lines = list(getattr(order, "ops_matched_lines", []))
    ret_qty = returned_qty_map(order)
    matched_lines: list[OpsOrderMatchLineOut] = []
    suppliers: list[str] = []
    seen_suppliers: set[str] = set()

    for line in lines:
        variant = getattr(line, "variant", None)
        product = getattr(variant, "product", None) if variant is not None else None
        brand = getattr(product, "brand", None) if product is not None else None
        offer = getattr(line, "offer", None)
        allocs = [
            a for a in getattr(line, "ops_active_allocs", [])
            if getattr(a, "status", "") != InventoryAllocation.Status.RELEASED
        ]
        allocated_qty = sum(int(a.qty) for a in allocs)
        # Prefer the line's offer; fall back to the first active allocation.
        warehouse = getattr(offer, "warehouse", None) if offer is not None else None
        if warehouse is None and allocs:
            warehouse = getattr(
                getattr(allocs[0], "inventory_item", None), "warehouse", None)
        thumbnail_url = _product_thumbnail_url(product)

        warehouse_code = (getattr(warehouse, "code", "") or "").strip()
        for a in allocs:
            wh = getattr(getattr(a, "inventory_item", None), "warehouse", None)
            wh_code = (getattr(wh, "code", "") or "").strip() if wh else ""
            if wh_code and wh_code not in seen_suppliers:
                seen_suppliers.add(wh_code)
                suppliers.append(wh_code)
        if warehouse_code and warehouse_code not in seen_suppliers:
            seen_suppliers.add(warehouse_code)
            suppliers.append(warehouse_code)

        deadline = ""
        if order.delivered_at:
            from datetime import timedelta
            d = (order.delivered_at.date()
                 if hasattr(order.delivered_at, "date")
                 else order.delivered_at)
            deadline = (d + timedelta(days=line_return_days(line))).isoformat()

        matched_lines.append(
            OpsOrderMatchLineOut(
                order_line_id=int(line.id),
                variant_id=(int(variant.id) if variant is not None else None),
                variant_sku=((getattr(variant, "sku", "") or "") if variant is not None else ""),
                barcode=((getattr(variant, "barcode", "") or "") if variant is not None else ""),
                sku=(line.sku or ""),
                name=(line.name or ""),
                qty=int(line.qty or 0),
                product_id=(int(product.id) if product is not None else None),
                product_name=((getattr(product, "name", "") or "") if product is not None else ""),
                product_slug=((getattr(product, "slug", "") or "") if product is not None else ""),
                thumbnail_url=thumbnail_url,
                warehouse_code=warehouse_code,
                warehouse_name=((getattr(warehouse, "name", "") or "") if warehouse is not None else ""),
                warehouse_country=((getattr(warehouse, "country_code", "") or "") if warehouse is not None else ""),
                warehouse_kind=((getattr(warehouse, "kind", "") or "") if warehouse is not None else ""),
                allocated_qty=allocated_qty,
                unallocated_qty=max(0, int(line.qty or 0) - allocated_qty),
                returnable=line_is_returnable(line),
                return_days=line_return_days(line),
                returned_qty=ret_qty.get(int(line.id), 0),
                return_deadline=deadline,
                brand_name=((getattr(brand, "name", "") or "") if brand is not None else ""),
                unit_net=_to_money_str(getattr(line, "unit_net", None)),
                unit_vat=_to_money_str(getattr(line, "unit_vat", None)),
                unit_gross=_to_money_str(getattr(line, "unit_gross", None)),
                line_total_gross=_to_money_str(getattr(line, "total_gross", None)),
            )
        )

    pi = getattr(order, "payment_intent", None)
    shop = getattr(order, "shop", None)
    user = getattr(order, "user", None)
    amount_gross = _effective_order_total_gross(order)
    return OpsOrderListItemOut(
        id=int(order.id),
        shop=OpsShopOut(
            id=(int(shop.id) if shop is not None else None),
            code=((getattr(shop, "code", "") or "") if shop is not None else ""),
            name=((getattr(shop, "name", "") or "") if shop is not None else ""),
            primary_domain=((getattr(shop, "primary_domain", "") or "") if shop is not None else ""),
        ),
        customer=OpsCustomerOut(
            user_id=(int(user.id) if user is not None else None),
            email=((getattr(user, "email", "") or "") if user is not None else ""),
            full_name=_customer_full_name(user, fallback=(order.shipping_full_name or "")),
            phone=(order.shipping_phone or ""),
            company=(order.shipping_company or ""),
            is_guest=(user is None),
        ),
        status=(order.status or ""),
        payment_status=((getattr(pi, "status", "") or "") if pi is not None else ""),
        delivery_status=(order.delivery_status or ""),
        fulfillment_mode=(order.fulfillment_mode or ""),
        supplier_reservation_status=(order.supplier_reservation_status or ""),
        supplier_reference=(order.supplier_reference or ""),
        country_code=(order.country_code or ""),
        shipping_method=(order.shipping_method or ""),
        shipping_country_code=(order.shipping_country_code or ""),
        delivery_min_date=_to_iso(order.delivery_min_date),
        delivery_max_date=_to_iso(order.delivery_max_date),
        created_at=_to_iso(order.created_at),
        amount_gross=_to_money_str(amount_gross),
        amount_currency=(order.currency or "EUR"),
        matched_items_count=int(getattr(order, "matched_items_count", len(lines))),
        matched_suppliers=suppliers,
        matched_lines=matched_lines,
    )


def _customer_order_stats(user_id: int | None, *, shop_id: int | None) -> OpsCustomerStatsOut:
    if not user_id:
        return OpsCustomerStatsOut()
    qs = Order.objects.filter(user_id=user_id)
    if shop_id is not None:
        qs = qs.filter(shop_id=shop_id)
    agg = qs.aggregate(
        orders_count=Count("id"),
        paid_orders_count=Count("id", filter=Q(status=Order.Status.PAID)),
        total_spent=Sum("total_gross", filter=Q(status=Order.Status.PAID)),
        last_order_at=Max("created_at"),
    )
    return OpsCustomerStatsOut(
        orders_count=int(agg.get("orders_count") or 0),
        paid_orders_count=int(agg.get("paid_orders_count") or 0),
        total_spent=_to_money_str(agg.get("total_spent")),
        last_order_at=_to_iso(agg.get("last_order_at")),
    )


def _serialize_ops_order_detail(order: Order) -> OpsOrderDetailOut:
    base = _serialize_ops_order(order)

    shipping_address = OpsOrderAddressOut(
        full_name=(order.shipping_full_name or ""),
        company=(order.shipping_company or ""),
        line1=(order.shipping_line1 or ""),
        city=(order.shipping_city or ""),
        postal_code=(order.shipping_postal_code or ""),
        country_code=(order.shipping_country_code or ""),
        phone=(order.shipping_phone or ""),
    )

    pickup_point = None
    if (order.pickup_point_id or "").strip() or (order.pickup_point_name or "").strip():
        pickup_point = OpsPickupPointOut(
            pickup_point_id=(order.pickup_point_id or ""),
            pickup_point_name=(order.pickup_point_name or ""),
            country_code=(
                (order.pickup_point_raw or {}).get("country_code")
                or (order.pickup_locker.country_code if order.pickup_locker_id else "")
                or ""
            ),
            raw=dict(order.pickup_point_raw or {}),
        )

    label_pdf_url = ""
    try:
        if order.shipping_label_pdf:
            label_pdf_url = order.shipping_label_pdf.url or ""
    except Exception:
        label_pdf_url = ""

    d_net = Decimal("0.00")
    d_vat = Decimal("0.00")
    d_gross = Decimal("0.00")
    for d in order.discounts.all():
        d_net += Decimal(d.net)
        d_vat += Decimal(d.vat)
        d_gross += Decimal(d.gross)

    totals = OpsOrderTotalsOut(
        items_net=_to_money_str(order.items_net),
        items_vat=_to_money_str(order.items_vat),
        items_gross=_to_money_str(order.items_gross),
        shipping_net=_to_money_str(order.shipping_net),
        shipping_vat=_to_money_str(order.shipping_vat),
        shipping_gross=_to_money_str(order.shipping_gross),
        discounts_net=_to_money_str(d_net),
        discounts_vat=_to_money_str(d_vat),
        discounts_gross=_to_money_str(d_gross),
        total_net=_to_money_str(order.total_net),
        total_vat=_to_money_str(order.total_vat),
        total_gross=_to_money_str(_effective_order_total_gross(order)),
        currency=(order.currency or "EUR"),
        paid_gross=_to_money_str(_paid_gross(order)),
        balance_due=_to_money_str(_balance_due(order)),
    )

    events = [
        OpsAuditEventOut(
            id=int(ev.id),
            order_id=int(ev.order_id),
            kind=(ev.kind or ""),
            name=(ev.name or ""),
            actor_label=(ev.actor_label or ""),
            payload=dict(ev.payload or {}),
            created_at=_to_iso(ev.created_at),
        )
        for ev in order.events.all()
    ]

    payments = [
        OpsPaymentOut(
            id=int(pi.id),
            order_id=int(pi.order_id),
            provider=(pi.provider or ""),
            status=(pi.status or ""),
            amount_gross=_to_money_str(pi.amount_gross),
            currency=(pi.currency or ""),
            external_id=(pi.external_id or ""),
            created_at=_to_iso(pi.created_at),
            updated_at=_to_iso(pi.updated_at),
        )
        for pi in PaymentIntent.objects.filter(order_id=order.id).order_by("-created_at", "-id")
    ]

    shop_id = int(order.shop_id) if order.shop_id else None
    return OpsOrderDetailOut(
        **base.model_dump(),
        totals=totals,
        shipping_address=shipping_address,
        pickup_point=pickup_point,
        tracking_number=(order.tracking_number or ""),
        carrier_code=(order.carrier_code or ""),
        carrier_shipment_id=(order.carrier_shipment_id or ""),
        label_pdf_url=label_pdf_url,
        label_generated_at=_to_iso(order.shipping_label_generated_at),
        delivered_at=_to_iso(order.delivered_at),
        updated_at=_to_iso(order.updated_at),
        supplier_notes=(order.supplier_notes or ""),
        events=events,
        payments=payments,
        customer_stats=_customer_order_stats(order.user_id, shop_id=shop_id),
        shipments=[_serialize_shipment(s) for s in order.shipments.all()],
        discounts=[
            OpsOrderDiscountOut(
                id=int(d.id),
                order_id=int(d.order_id),
                kind=d.kind or "",
                code=d.code or "",
                name=d.name or "",
                net=_to_money_str(d.net),
                vat=_to_money_str(d.vat),
                gross=_to_money_str(d.gross),
            )
            for d in order.discounts.all()
        ],
        surcharges=[
            OpsSurchargeOut(
                id=int(s.id),
                order_id=int(s.order_id),
                direction=s.direction,
                source=s.source,
                amount_gross=_to_money_str(s.amount_gross),
                currency=s.currency or "",
                method=s.method or "",
                status=s.status,
                reason=s.reason or "",
                payment_url=s.payment_url or "",
                emailed_at=_to_iso(s.emailed_at),
                paid_at=_to_iso(s.paid_at),
                created_at=_to_iso(s.created_at),
            )
            for s in order.surcharges.all()
        ],
    )


def _apply_order_action(order: Order, *, action: str, user, assignee: str = "", note: str = "") -> None:
    now = timezone.now()
    action = (action or "").strip().lower()

    if action == "confirm":
        if order.supplier_reservation_status == Order.SupplierReservationStatus.NOT_NEEDED:
            order.supplier_reservation_status = Order.SupplierReservationStatus.PENDING
            order.save(update_fields=["supplier_reservation_status", "updated_at"])
        _track_order_event(
            order=order,
            user=user,
            kind=OrderEvent.Kind.FULFILLMENT,
            name="ops_confirmed",
            payload={"note": note},
        )
        return

    if action == "assign":
        _track_order_event(
            order=order,
            user=user,
            kind=OrderEvent.Kind.NOTE,
            name="ops_assigned",
            payload={"assignee": assignee, "note": note},
        )
        return

    if action == "move_to_picking":
        order.supplier_reservation_status = Order.SupplierReservationStatus.RESERVED
        if order.supplier_reserved_at is None:
            order.supplier_reserved_at = now
        order.save(update_fields=["supplier_reservation_status", "supplier_reserved_at", "updated_at"])
        _track_order_event(
            order=order,
            user=user,
            kind=OrderEvent.Kind.FULFILLMENT,
            name="ops_moved_to_picking",
            payload={"note": note},
        )
        return

    raise HttpError(400, f"Unsupported action '{action}'")


def _execute_ops_job(job: OpsJob, *, user) -> OpsJob:
    payload = dict(job.request_payload or {})
    now = timezone.now()
    job.status = OpsJob.Status.RUNNING
    job.started_at = now
    job.error_message = ""
    job.result_payload = {}
    job.save(update_fields=["status", "started_at", "error_message", "result_payload", "updated_at"])

    try:
        if job.job_type == OpsJob.JobType.ORDERS_BULK_ACTION:
            action = str(payload.get("action") or "").strip().lower()
            selected_ids = [int(x) for x in (payload.get("selected_ids") or []) if str(x).strip()]
            assignee = str(payload.get("assignee") or "")
            note = str(payload.get("note") or "")

            order_qs = Order.objects.filter(id__in=selected_ids)
            if job.shop_id is not None:
                order_qs = order_qs.filter(shop_id=job.shop_id)
            else:
                order_qs = order_qs.filter(shop__isnull=True)
            orders = list(order_qs)

            updated_ids: list[int] = []
            failed_ids: list[int] = []
            for order in orders:
                try:
                    _apply_order_action(order, action=action, user=user, assignee=assignee, note=note)
                    updated_ids.append(int(order.id))
                except HttpError:
                    failed_ids.append(int(order.id))

            job.status = OpsJob.Status.SUCCESS
            job.result_payload = {
                "action": action,
                "updated_ids": updated_ids,
                "failed_ids": failed_ids,
            }

        elif job.job_type == OpsJob.JobType.SHIPMENT_LABELS_BULK:
            order_ids = [int(x) for x in (payload.get("order_ids") or []) if str(x).strip()]
            order_qs = Order.objects.filter(id__in=order_ids)
            if job.shop_id is not None:
                order_qs = order_qs.filter(shop_id=job.shop_id)
            else:
                order_qs = order_qs.filter(shop__isnull=True)
            orders = list(order_qs.select_related("pickup_locker"))

            updated_ids: list[int] = []
            failed: list[dict] = []
            for order in orders:
                try:
                    _generate_order_label(order, user=user)
                    updated_ids.append(int(order.id))
                except Exception as exc:  # noqa: BLE001
                    failed.append({"order_id": int(order.id), "error": str(exc)})

            job.status = OpsJob.Status.SUCCESS if not failed else OpsJob.Status.FAILED
            job.result_payload = {"updated_ids": updated_ids, "failed": failed}
            if failed:
                job.error_message = f"Failed for {len(failed)} order(s)"
        else:
            raise ValueError(f"Unsupported job type '{job.job_type}'")

    except Exception as exc:  # noqa: BLE001
        job.status = OpsJob.Status.FAILED
        job.error_message = str(exc)
    finally:
        job.completed_at = timezone.now()
        job.save(
            update_fields=[
                "status",
                "result_payload",
                "error_message",
                "completed_at",
                "updated_at",
            ]
        )
    return job


def _get_or_create_primary_shipment(order: Order, *, user=None) -> Shipment:
    shipment = (
        order.shipments.filter(
            kind=Shipment.Kind.OUTBOUND,
        )
        .exclude(status=Shipment.Status.CANCELLED)
        .order_by("-created_at", "-id")
        .first()
    )
    if shipment is None:
        shipment = Shipment.objects.create(
            order=order,
            kind=Shipment.Kind.OUTBOUND,
            status=order.delivery_status or Shipment.Status.PENDING,
            carrier_code=order.carrier_code or "",
            shipping_method=order.shipping_method or "",
            tracking_number=order.tracking_number or "",
            carrier_shipment_id=order.carrier_shipment_id or "",
            pickup_locker=order.pickup_locker,
            pickup_point_id=order.pickup_point_id or "",
            pickup_point_name=order.pickup_point_name or "",
            created_by=user,
        )
    return shipment


def _order_detail_qs(order_id: int) -> Order:
    """Order re-fetched with the prefetches _serialize_ops_order_detail needs."""
    return (
        Order.objects.filter(id=order_id)
        .select_related("payment_intent", "shop", "user", "pickup_locker")
        .annotate(matched_items_count=Count("lines", distinct=True))
        .prefetch_related(
            Prefetch(
                "lines",
                queryset=OrderLine.objects.select_related(
                    "variant__product__brand", "offer__warehouse")
                .prefetch_related(
                    "variant__product__images",
                    Prefetch(
                        "inventory_allocations",
                        queryset=InventoryAllocation.objects
                        .exclude(status=InventoryAllocation.Status.RELEASED)
                        .select_related("inventory_item__warehouse"),
                        to_attr="ops_active_allocs",
                    ),
                )
                .order_by("id"),
                to_attr="ops_matched_lines",
            ),
            "events",
            "discounts",
        )
        .first()
    )


def _get_surcharge_for_ops(surcharge_id: int, *, shop_id: int | None) -> Surcharge:
    qs = Surcharge.objects.select_related("order")
    if shop_id is not None:
        qs = qs.filter(order__shop_id=shop_id)
    else:
        qs = qs.filter(order__shop__isnull=True)
    s = qs.filter(id=surcharge_id).first()
    if s is None:
        raise HttpError(404, "Surcharge not found")
    return s


def _get_shipment_for_ops(shipment_id: int, *, shop_id: int | None) -> Shipment:
    qs = Shipment.objects.select_related("order")
    if shop_id is not None:
        qs = qs.filter(order__shop_id=shop_id)
    else:
        qs = qs.filter(order__shop__isnull=True)
    shipment = qs.filter(id=shipment_id).first()
    if shipment is None:
        raise HttpError(404, "Shipment not found")
    return shipment


def _replace_shipment_for_carrier_change(
    *,
    order: Order,
    shipping_method: str,
    pickup_locker=None,
    pickup_point_id: str = "",
    pickup_point_name: str = "",
    user=None,
) -> Shipment:
    """Carrier/destination change -> a NEW outbound shipment (PrestaShop DPD
    style). The previous one is cancelled when it was not yet handed to a
    carrier; if it is already on the way it is preserved untouched."""
    previous = (
        order.shipments.filter(kind=Shipment.Kind.OUTBOUND)
        .exclude(status=Shipment.Status.CANCELLED)
        .order_by("-created_at", "-id")
        .first()
    )
    if previous is not None and previous.status in {
        Shipment.Status.PENDING,
        Shipment.Status.PACKED,
    }:
        previous.status = Shipment.Status.CANCELLED
        previous.notes = (
            (previous.notes or "")
            + ("" if not previous.notes else " ")
            + "Cancelled: shipping method changed"
        ).strip()
        previous.save(update_fields=["status", "notes", "updated_at"])

    shipment = Shipment.objects.create(
        order=order,
        kind=Shipment.Kind.OUTBOUND,
        status=Shipment.Status.PENDING,
        shipping_method=shipping_method or "",
        pickup_locker=pickup_locker,
        pickup_point_id=pickup_point_id or "",
        pickup_point_name=pickup_point_name or "",
        created_by=user,
    )
    shipment.apply_to_order_projection()
    return shipment


def _fork_labelled_shipment(shipment: Shipment, *, user=None) -> Shipment:
    """Label already generated -> clone into a fresh shipment so a new
    label gets its own tracking number; the old label stays historical."""
    fresh = Shipment.objects.create(
        order=shipment.order,
        kind=shipment.kind,
        status=Shipment.Status.PENDING,
        shipping_method=shipment.shipping_method,
        pickup_locker=shipment.pickup_locker,
        pickup_point_id=shipment.pickup_point_id,
        pickup_point_name=shipment.pickup_point_name,
        notes=shipment.notes,
        created_by=user,
    )
    old_status = shipment.status
    if old_status in {Shipment.Status.PENDING, Shipment.Status.PACKED}:
        shipment.status = Shipment.Status.CANCELLED
        shipment.notes = (
            (shipment.notes or "")
            + ("" if not shipment.notes else " ")
            + "Cancelled: label regenerated"
        ).strip()
        shipment.save(update_fields=["status", "notes", "updated_at"])
    fresh.apply_to_order_projection()
    return fresh


def _generate_shipment_label(shipment: Shipment, *, user) -> Shipment:
    if shipment.label_generated_at:
        shipment = _fork_labelled_shipment(shipment, user=user)
    order = shipment.order
    shipping_method = (
        shipment.shipping_method or order.shipping_method or ""
    ).strip().lower()

    if shipping_method in {"dpd_locker", "dpd_courier"}:
        from dpd.labels import generate_a6_label_pdf_for_order

        generate_a6_label_pdf_for_order(
            order, store_on_order=False, shipment=shipment)
    elif shipping_method in {"unisend_pickup", "unisend_courier", "lpexpress", "lpexpress_courier"}:
        from unisend.labels import generate_label_pdf_for_order

        # Unisend writes tracking/label to the order — mirror onto the shipment.
        generate_label_pdf_for_order(order, store_on_order=False)
        if order.tracking_number and not shipment.tracking_number:
            shipment.tracking_number = order.tracking_number
        shipment.carrier_code = order.carrier_code or "unisend"
        shipment.carrier_shipment_id = order.carrier_shipment_id or ""
        shipment.status = "packed"
        shipment.label_generated_at = timezone.now()
        shipment.save(update_fields=[
            "tracking_number", "carrier_code", "carrier_shipment_id",
            "status", "label_generated_at", "updated_at",
        ])
        shipment.apply_to_order_projection()
    else:
        raise HttpError(400, f"Shipping method '{shipping_method}' is not supported for label generation")

    _track_order_event(
        order=order,
        user=user,
        kind=OrderEvent.Kind.DELIVERY,
        name="ops_label_generated",
        payload={
            "shipment_id": shipment.id,
            "shipping_method": shipping_method,
            "carrier_code": shipment.carrier_code,
            "carrier_shipment_id": shipment.carrier_shipment_id,
        },
    )
    return shipment


def _serialize_shipment(s: Shipment) -> OpsShipmentRowOut:
    label_url = ""
    if s.shipping_label_pdf:
        try:
            label_url = s.shipping_label_pdf.url
        except Exception:
            label_url = ""
    return OpsShipmentRowOut(
        id=int(s.id),
        order_id=int(s.order_id),
        kind=s.kind,
        status=s.status,
        shipping_method=s.shipping_method or "",
        carrier_code=s.carrier_code or "",
        carrier_shipment_id=s.carrier_shipment_id or "",
        tracking_number=s.tracking_number or "",
        pickup_locker_id=int(s.pickup_locker_id) if s.pickup_locker_id else None,
        pickup_point_id=s.pickup_point_id or "",
        pickup_point_name=s.pickup_point_name or "",
        label_url=label_url,
        label_generated_at=_to_iso(s.label_generated_at),
        notes=s.notes or "",
        created_at=_to_iso(s.created_at),
        updated_at=_to_iso(s.updated_at),
    )


def _paid_gross(order: Order) -> Decimal:
    from checkout.surcharges import paid_amount_gross

    return paid_amount_gross(order=order)


def _balance_due(order: Order) -> Decimal:
    from checkout.surcharges import balance_due

    return balance_due(order=order)


def _generate_order_label(order: Order, *, user):
    shipping_method = (order.shipping_method or "").strip().lower()

    if shipping_method in {"dpd_locker", "dpd_courier", "unisend_pickup",
                           "unisend_courier", "lpexpress", "lpexpress_courier"}:
        # Route through the shipment layer; the primary outbound shipment
        # mirrors back onto the order for backward compatibility.
        shipment = _get_or_create_primary_shipment(order, user=user)
        _generate_shipment_label(shipment, user=user)
    else:
        raise HttpError(400, f"Shipping method '{shipping_method}' is not supported for label generation")


def _serialize_job(job: OpsJob) -> OpsJobOut:
    return OpsJobOut(
        id=int(job.id),
        job_type=job.job_type,
        status=job.status,
        shop_id=(int(job.shop_id) if job.shop_id else None),
        actor_user_id=(int(job.actor_user_id) if job.actor_user_id else None),
        request_payload=dict(job.request_payload or {}),
        result_payload=dict(job.result_payload or {}),
        error_message=(job.error_message or ""),
        started_at=_to_iso(job.started_at),
        completed_at=_to_iso(job.completed_at),
        created_at=_to_iso(job.created_at),
        updated_at=_to_iso(job.updated_at),
    )


# Re-export everything (incl. underscore helpers) so endpoint modules can use
# `from .shared import *` after the mechanical split of the original ops_api.py.
__all__ = [n for n in dir() if not n.startswith("__")]
