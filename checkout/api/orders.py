from __future__ import annotations

from django.http import FileResponse

from api.i18n import get_request_language_code
from checkout.statuses import get_status_label, get_status_options

from .shared import *


def _localized_status_labels(o, pi, language_code: str) -> dict[str, str]:
    # Locker pickups show "picked up from locker" instead of plain "delivered".
    ds_code = o.delivery_status
    if ds_code == Order.DeliveryStatus.DELIVERED and (
        "pickup" in (o.shipping_method or "").lower()
        or "locker" in (o.shipping_method or "").lower()
        or o.pickup_locker_id is not None
        or (o.pickup_point_id or "").strip()
    ):
        ds_code = "delivered_pickup"
    return {
        "status_label": get_status_label("order_status", o.status, language_code),
        "delivery_status_label": get_status_label("delivery_status", ds_code, language_code),
        "fulfillment_mode_label": get_status_label("fulfillment_mode", o.fulfillment_mode, language_code),
        "supplier_reservation_status_label": get_status_label(
            "supplier_reservation_status", o.supplier_reservation_status, language_code),
        "payment_provider_label": get_status_label("payment_provider", pi.provider, language_code) if pi else "",
        "payment_status_label": get_status_label("payment_status", pi.status, language_code) if pi else "",
    }


@router.get("/statuses", auth=_auth)
def get_order_statuses(request):
    language_code = get_request_language_code(request)
    return {
        domain: get_status_options(domain, language_code)
        for domain in (
            "order_status",
            "delivery_status",
            "fulfillment_mode",
            "supplier_reservation_status",
            "payment_status",
            "payment_provider",
        )
    }


@router.get("/orders", response=list[OrderOut], auth=_auth)
def list_orders(request, limit: int = 20):
    _ensure_shop_context_if_strict(request)
    user = _require_user(request)
    limit = max(1, min(int(limit or 20), 50))
    shop_id = _request_shop_id(request)

    order_qs = Order.objects.filter(user=user)
    if shop_id is not None:
        order_qs = order_qs.filter(shop_id=shop_id)
    else:
        order_qs = order_qs.filter(shop__isnull=True)

    orders = (
        order_qs
        .select_related("payment_intent")
        .prefetch_related("lines", "fees", "discounts")
        .order_by("-created_at")[:limit]
    )

    language_code = get_request_language_code(request)

    result: list[OrderOut] = []
    for o in orders:
        pi = getattr(o, "payment_intent", None)
        payment_instructions = _payment_instructions_for_order(order=o)

        labels = _localized_status_labels(o, pi, language_code)

        fees_out: list[FeeOut] = []
        fees_net = Decimal("0.00")
        fees_vat = Decimal("0.00")
        fees_gross = Decimal("0.00")
        for f in o.fees.all():
            fees_net += Decimal(f.net)
            fees_vat += Decimal(f.vat)
            fees_gross += Decimal(f.gross)
            fees_out.append(
                FeeOut(
                    code=f.code,
                    name=f.name,
                    amount=MoneyOut(
                        currency=o.currency,
                        net=f.net,
                        vat_rate=f.vat_rate,
                        vat=f.vat,
                        gross=f.gross,
                    ),
                )
            )

        lines_out: list[OrderLineOut] = []
        agg_min = None
        agg_max = None
        agg_meta: dict | None = None
        for ln in o.lines.all():
            unit = MoneyOut(currency=o.currency, net=ln.unit_net,
                            vat_rate=ln.vat_rate, vat=ln.unit_vat, gross=ln.unit_gross)
            total = MoneyOut(currency=o.currency, net=ln.total_net,
                             vat_rate=ln.vat_rate, vat=ln.total_vat, gross=ln.total_gross)

            compare_at_unit = None
            compare_at_total = None
            disc_pct = None
            try:
                list_unit_net = Decimal(getattr(getattr(ln, "variant", None), "price_eur", 0) or 0)
                base_unit_net = (
                    _effective_offer_unit_net(list_unit_net=list_unit_net, offer=ln.offer)
                    if getattr(ln, "offer_id", None)
                    else list_unit_net
                )
                disc_pct = _discount_percent(list_unit_net=base_unit_net, sale_unit_net=ln.unit_net)
                if disc_pct is not None:
                    base_u = money_from_net(currency=o.currency, unit_net=base_unit_net, vat_rate=ln.vat_rate, qty=1)
                    base_t = money_from_net(currency=o.currency, unit_net=base_unit_net, vat_rate=ln.vat_rate, qty=int(ln.qty))
                    compare_at_unit = MoneyOut(
                        currency=base_u.currency,
                        net=base_u.net,
                        vat_rate=base_u.vat_rate,
                        vat=base_u.vat,
                        gross=base_u.gross,
                    )
                    compare_at_total = MoneyOut(
                        currency=base_t.currency,
                        net=base_t.net,
                        vat_rate=base_t.vat_rate,
                        vat=base_t.vat,
                        gross=base_t.gross,
                    )
            except Exception:
                pass

            lines_out.append(
                OrderLineOut(
                    id=ln.id,
                    sku=ln.sku,
                    name=ln.name,
                    qty=ln.qty,
                    unit_price=unit,
                    compare_at_unit_price=compare_at_unit,
                    discount_percent=disc_pct,
                    line_total=total,
                    compare_at_line_total=compare_at_total,
                    delivery_window=None,
                )
            )

            # Per-line delivery window
            dw = None
            try:
                v = getattr(ln, "variant", None)
                p = getattr(v, "product", None) if v is not None else None
                line_channel = (
                    "outlet"
                    if getattr(ln, "offer_id", None)
                    and ln.offer
                    and getattr(ln.offer, "offer_visibility", None) == InventoryItem.OfferVisibility.OUTLET
                    else "normal"
                )
                dw = estimate_delivery_window(
                    now=timezone.now(),
                    country_code=o.country_code,
                    channel=line_channel,
                    warehouse_id=int(ln.offer.warehouse_id) if getattr(ln, "offer_id", None) and ln.offer and ln.offer.warehouse_id else None,
                    product_id=int(p.id) if p else None,
                    brand_id=int(p.brand_id) if p and p.brand_id else None,
                    category_id=int(p.category_id) if p and p.category_id else None,
                    product_group_id=int(getattr(p, "group_id", None)) if p and getattr(p, "group_id", None) else None,
                )
            except Exception:
                dw = None

            if dw is not None:
                dw_out = {
                    "min_date": dw.min_date.isoformat(),
                    "max_date": dw.max_date.isoformat(),
                    "kind": dw.kind,
                    "rule_code": dw.rule_code,
                    "source": dw.source,
                }
                lines_out[-1].delivery_window = dw_out
                if agg_min is None or dw.min_date > agg_min:
                    agg_min = dw.min_date
                if agg_max is None or dw.max_date > agg_max:
                    agg_max = dw.max_date
                agg_meta = dw_out

        items_total = MoneyOut(currency=o.currency, net=o.items_net, vat_rate=Decimal(
            "0"), vat=o.items_vat, gross=o.items_gross)
        shipping_total = MoneyOut(currency=o.currency, net=o.shipping_net, vat_rate=Decimal(
            "0"), vat=o.shipping_vat, gross=o.shipping_gross)
        fees_total = MoneyOut(currency=o.currency, net=fees_net, vat_rate=Decimal(
            "0"), vat=fees_vat, gross=fees_gross)

        disc_net = Decimal("0.00")
        disc_vat = Decimal("0.00")
        disc_gross = Decimal("0.00")
        for d in o.discounts.all():
            disc_net += Decimal(d.net)
            disc_vat += Decimal(d.vat)
            disc_gross += Decimal(d.gross)
        discount_total = MoneyOut(currency=o.currency, net=disc_net, vat_rate=Decimal("0"), vat=disc_vat, gross=disc_gross)

        order_total = MoneyOut(currency=o.currency, net=o.total_net, vat_rate=Decimal(
            "0"), vat=o.total_vat, gross=o.total_gross)

        delivery_window = None
        if getattr(o, "delivery_min_date", None) and getattr(o, "delivery_max_date", None):
            delivery_window = {
                "min_date": o.delivery_min_date.isoformat(),
                "max_date": o.delivery_max_date.isoformat(),
                "kind": str(getattr(o, "delivery_eta_kind", "") or "estimated"),
                "rule_code": str(getattr(o, "delivery_eta_rule_code", "") or ""),
                "source": str(getattr(o, "delivery_eta_source", "") or ""),
            }
        elif agg_min is not None and agg_max is not None:
            # Backward-compatible fallback if snapshot was not stored (older orders).
            delivery_window = {
                "min_date": agg_min.isoformat(),
                "max_date": agg_max.isoformat(),
                "kind": (str((agg_meta or {}).get("kind") or "estimated")),
                "rule_code": str((agg_meta or {}).get("rule_code") or ""),
                "source": str((agg_meta or {}).get("source") or ""),
            }

        result.append(
            OrderOut(
                id=o.id,
                status=o.status,
                status_label=labels["status_label"],
                delivery_status=o.delivery_status,
                delivery_status_label=labels["delivery_status_label"],
                fulfillment_mode=(getattr(o, "fulfillment_mode", "") or ""),
                fulfillment_mode_label=labels["fulfillment_mode_label"],
                supplier_reservation_status=(getattr(o, "supplier_reservation_status", "") or ""),
                supplier_reservation_status_label=labels["supplier_reservation_status_label"],
                supplier_reserved_at=(o.supplier_reserved_at.isoformat() if getattr(o, "supplier_reserved_at", None) else ""),
                supplier_reference=(getattr(o, "supplier_reference", "") or ""),
                currency=o.currency,
                country_code=o.country_code,
                shipping_method=o.shipping_method,
                carrier_code=o.carrier_code,
                tracking_number=o.tracking_number,
                payment_provider=(pi.provider if pi else ""),
                payment_provider_label=labels["payment_provider_label"],
                payment_status=(pi.status if pi else ""),
                payment_status_label=labels["payment_status_label"],
                payment_redirect_url=(pi.redirect_url if pi else ""),
                payment_instructions=payment_instructions,
                items=lines_out,
                delivery_window=delivery_window,
                items_total=items_total,
                discount_total=discount_total,
                shipping_total=shipping_total,
                fees_total=fees_total,
                fees=fees_out,
                order_total=order_total,
                created_at=o.created_at.isoformat(),
            )
        )

    return result


@router.get("/orders/{order_id}", response=OrderOut, auth=_auth)
def get_order(request, order_id: int):
    _ensure_shop_context_if_strict(request)
    user = _require_user(request)
    shop_id = _request_shop_id(request)

    order_qs = Order.objects.filter(user=user, id=order_id)
    if shop_id is not None:
        order_qs = order_qs.filter(shop_id=shop_id)
    else:
        order_qs = order_qs.filter(shop__isnull=True)

    o = (
        order_qs
        .select_related("payment_intent")
        .prefetch_related("lines", "fees", "discounts")
        .first()
    )
    if not o:
        raise HttpError(404, "Order not found")

    return _build_order_detail(o, get_request_language_code(request))


@router.get("/orders/{order_id}/invoice", auth=_auth)
def download_order_invoice(request, order_id: int):
    """Latest issued sales invoice PDF for the customer's own order."""
    _ensure_shop_context_if_strict(request)
    user = _require_user(request)
    shop_id = _request_shop_id(request)

    order_qs = Order.objects.filter(user=user, id=order_id)
    order_qs = order_qs.filter(shop_id=shop_id) if shop_id is not None else order_qs.filter(shop__isnull=True)
    order = order_qs.first()
    if order is None:
        raise HttpError(404, "Order not found")

    from billing.models import Invoice

    invoice = (
        Invoice.objects.filter(order=order, kind=Invoice.Kind.SALES,
                               status=Invoice.Status.ISSUED)
        .exclude(pdf="")
        .order_by("-issue_date", "-id")
        .first()
    )
    if invoice is None:
        raise HttpError(404, "Invoice not issued yet")
    return FileResponse(
        invoice.pdf.open("rb"),
        content_type="application/pdf",
        filename=f"{invoice.number}.pdf",
    )


def _build_order_detail(o, language_code: str):
    lines_out: list[OrderLineOut] = []
    agg_min = None
    agg_max = None
    agg_meta: dict | None = None
    for ln in o.lines.all():
        unit = MoneyOut(currency=o.currency, net=ln.unit_net,
                        vat_rate=ln.vat_rate, vat=ln.unit_vat, gross=ln.unit_gross)
        total = MoneyOut(currency=o.currency, net=ln.total_net,
                         vat_rate=ln.vat_rate, vat=ln.total_vat, gross=ln.total_gross)
        lines_out.append(OrderLineOut(id=ln.id, sku=ln.sku, name=ln.name,
                         qty=ln.qty, unit_price=unit, line_total=total, delivery_window=None))

        dw = None
        try:
            v = getattr(ln, "variant", None)
            p = getattr(v, "product", None) if v is not None else None
            line_channel = (
                "outlet"
                if getattr(ln, "offer_id", None)
                and ln.offer
                and getattr(ln.offer, "offer_visibility", None) == InventoryItem.OfferVisibility.OUTLET
                else "normal"
            )
            dw = estimate_delivery_window(
                now=timezone.now(),
                country_code=o.country_code,
                channel=line_channel,
                warehouse_id=int(ln.offer.warehouse_id) if getattr(ln, "offer_id", None) and ln.offer and ln.offer.warehouse_id else None,
                product_id=int(p.id) if p else None,
                brand_id=int(p.brand_id) if p and p.brand_id else None,
                category_id=int(p.category_id) if p and p.category_id else None,
                product_group_id=int(getattr(p, "group_id", None)) if p and getattr(p, "group_id", None) else None,
            )
        except Exception:
            dw = None

        if dw is not None:
            dw_out = {
                "min_date": dw.min_date.isoformat(),
                "max_date": dw.max_date.isoformat(),
                "kind": dw.kind,
                "rule_code": dw.rule_code,
                "source": dw.source,
            }
            lines_out[-1].delivery_window = dw_out
            if agg_min is None or dw.min_date > agg_min:
                agg_min = dw.min_date
            if agg_max is None or dw.max_date > agg_max:
                agg_max = dw.max_date
            agg_meta = dw_out

    items_total = MoneyOut(currency=o.currency, net=o.items_net, vat_rate=Decimal(
        "0"), vat=o.items_vat, gross=o.items_gross)
    shipping_total = MoneyOut(currency=o.currency, net=o.shipping_net, vat_rate=Decimal(
        "0"), vat=o.shipping_vat, gross=o.shipping_gross)

    fees_out: list[FeeOut] = []
    fees_net = Decimal("0.00")
    fees_vat = Decimal("0.00")
    fees_gross = Decimal("0.00")
    for f in o.fees.all():
        fees_net += Decimal(f.net)
        fees_vat += Decimal(f.vat)
        fees_gross += Decimal(f.gross)
        fees_out.append(
            FeeOut(
                code=f.code,
                name=f.name,
                amount=MoneyOut(
                    currency=o.currency,
                    net=f.net,
                    vat_rate=f.vat_rate,
                    vat=f.vat,
                    gross=f.gross,
                ),
            )
        )

    fees_total = MoneyOut(currency=o.currency, net=fees_net, vat_rate=Decimal(
        "0"), vat=fees_vat, gross=fees_gross)

    disc_net = Decimal("0.00")
    disc_vat = Decimal("0.00")
    disc_gross = Decimal("0.00")
    for d in o.discounts.all():
        disc_net += Decimal(d.net)
        disc_vat += Decimal(d.vat)
        disc_gross += Decimal(d.gross)
    discount_total = MoneyOut(currency=o.currency, net=disc_net, vat_rate=Decimal("0"), vat=disc_vat, gross=disc_gross)

    order_total = MoneyOut(currency=o.currency, net=o.total_net, vat_rate=Decimal(
        "0"), vat=o.total_vat, gross=o.total_gross)

    pi = getattr(o, "payment_intent", None)
    payment_instructions = _payment_instructions_for_order(order=o)

    labels = _localized_status_labels(o, pi, language_code)

    delivery_window = None
    if getattr(o, "delivery_min_date", None) and getattr(o, "delivery_max_date", None):
        delivery_window = {
            "min_date": o.delivery_min_date.isoformat(),
            "max_date": o.delivery_max_date.isoformat(),
            "kind": str(getattr(o, "delivery_eta_kind", "") or "estimated"),
            "rule_code": str(getattr(o, "delivery_eta_rule_code", "") or ""),
            "source": str(getattr(o, "delivery_eta_source", "") or ""),
        }
    elif agg_min is not None and agg_max is not None:
        delivery_window = {
            "min_date": agg_min.isoformat(),
            "max_date": agg_max.isoformat(),
            "kind": (str((agg_meta or {}).get("kind") or "estimated")),
            "rule_code": str((agg_meta or {}).get("rule_code") or ""),
            "source": str((agg_meta or {}).get("source") or ""),
        }

    invoice_number = ""
    if getattr(o, "delivered_at", None):
        from billing.models import Invoice
        inv = Invoice.objects.filter(
            order_id=o.id, kind=Invoice.Kind.SALES,
            status=Invoice.Status.ISSUED).values_list("number", flat=True).first()
        invoice_number = inv or ""

    return OrderOut(
        id=o.id,
        status=o.status,
        status_label=labels["status_label"],
        delivery_status=o.delivery_status,
        delivery_status_label=labels["delivery_status_label"],
        fulfillment_mode=(getattr(o, "fulfillment_mode", "") or ""),
        fulfillment_mode_label=labels["fulfillment_mode_label"],
        supplier_reservation_status=(getattr(o, "supplier_reservation_status", "") or ""),
        supplier_reservation_status_label=labels["supplier_reservation_status_label"],
        supplier_reserved_at=(o.supplier_reserved_at.isoformat() if getattr(o, "supplier_reserved_at", None) else ""),
        supplier_reference=(getattr(o, "supplier_reference", "") or ""),
        currency=o.currency,
        country_code=o.country_code,
        shipping_method=o.shipping_method,
        carrier_code=o.carrier_code,
        tracking_number=o.tracking_number,
        delivered_at=(o.delivered_at.isoformat() if o.delivered_at else ""),
        invoice_number=invoice_number,
        payment_provider=(pi.provider if pi else ""),
        payment_provider_label=labels["payment_provider_label"],
        payment_status=(pi.status if pi else ""),
        payment_status_label=labels["payment_status_label"],
        payment_redirect_url=(pi.redirect_url if pi else ""),
        payment_instructions=payment_instructions,
        items=lines_out,
        delivery_window=delivery_window,
        items_total=items_total,
        discount_total=discount_total,
        shipping_total=shipping_total,
        fees_total=fees_total,
        fees=fees_out,
        order_total=order_total,
        created_at=o.created_at.isoformat(),
    )
