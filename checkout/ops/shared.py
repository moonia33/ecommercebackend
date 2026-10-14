from __future__ import annotations

from datetime import date
from decimal import Decimal

from django.conf import settings
from django.db import transaction
from django.db.models import Count, Prefetch, Q, Sum
from django.utils import timezone
from ninja import Query, Router, Schema
from ninja.errors import HttpError

from accounts.auth import JWTAuth
from catalog.models import InventoryItem, Product, Variant
from checkout.models import OpsJob, Order, OrderEvent, OrderLine, PaymentIntent
from checkout.services import capture_inventory_for_order, release_inventory_for_order
from promotions.services import redeem_coupon_for_paid_order, release_coupon_for_order


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


class OpsVariantPriceUpdateIn(Schema):
    price_eur: Decimal


class OpsOfferVisibilityIn(Schema):
    visibility: str


class OpsCatalogBulkActionIn(Schema):
    action: str
    product_ids: list[int] = []


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
    product_name: str
    warehouse_code: str
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
    category_name: str = ""
    variants_count: int


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
            | Q(**{f"{p}variant__product__name__icontains": product_q})
            | Q(**{f"{p}variant__product__slug__icontains": product_q})
        )

    supplier_code = (filters.supplier_code or "").strip()
    if supplier_code:
        line_q &= Q(**{f"{p}offer__warehouse__code__iexact": supplier_code})

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
    lines = list(getattr(order, "ops_matched_lines", []))
    matched_lines: list[OpsOrderMatchLineOut] = []
    suppliers: list[str] = []
    seen_suppliers: set[str] = set()

    for line in lines:
        variant = getattr(line, "variant", None)
        product = getattr(variant, "product", None) if variant is not None else None
        brand = getattr(product, "brand", None) if product is not None else None
        offer = getattr(line, "offer", None)
        warehouse = getattr(offer, "warehouse", None) if offer is not None else None
        thumbnail_url = _product_thumbnail_url(product)

        warehouse_code = (getattr(warehouse, "code", "") or "").strip()
        if warehouse_code and warehouse_code not in seen_suppliers:
            seen_suppliers.add(warehouse_code)
            suppliers.append(warehouse_code)

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


def _generate_order_label(order: Order, *, user):
    shipping_method = (order.shipping_method or "").strip().lower()

    if shipping_method in {"dpd_locker", "dpd_courier"}:
        from dpd.labels import generate_a6_label_pdf_for_order

        generate_a6_label_pdf_for_order(order, store_on_order=True)
    elif shipping_method in {"unisend_pickup", "unisend_courier", "lpexpress", "lpexpress_courier"}:
        from unisend.labels import generate_label_pdf_for_order

        generate_label_pdf_for_order(order, store_on_order=True)
    else:
        raise HttpError(400, f"Shipping method '{shipping_method}' is not supported for label generation")

    _track_order_event(
        order=order,
        user=user,
        kind=OrderEvent.Kind.DELIVERY,
        name="ops_label_generated",
        payload={
            "shipping_method": shipping_method,
            "carrier_code": order.carrier_code,
            "carrier_shipment_id": order.carrier_shipment_id,
        },
    )


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
