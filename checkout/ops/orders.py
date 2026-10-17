from __future__ import annotations

from .shared import *


@router.get("/orders", response=OpsOrderSearchOut, auth=_auth)
def ops_list_orders(request, filters: OpsOrderSearchIn = Query(...)):
    _ensure_shop_context_if_strict(request)
    _require_ops_user(request)

    page = max(1, int(filters.page or 1))
    page_size = max(1, min(int(filters.page_size or 50), 100))
    offset = (page - 1) * page_size

    shop_id = _request_shop_id(request)
    order_q, line_q = _build_order_filter(filters, shop_id=shop_id)
    order_line_q = _build_line_filter(filters, prefix="lines")
    has_line_filters = any(
        [
            bool((filters.product_q or "").strip()),
            bool((filters.supplier_code or "").strip()),
            bool((filters.manufacturer_q or "").strip()),
        ]
    )

    qs = Order.objects.filter(order_q).select_related("payment_intent", "shop", "user")
    if has_line_filters:
        qs = qs.filter(order_line_q)
    qs = qs.distinct()

    if has_line_filters:
        qs = qs.annotate(
            matched_items_count=Count("lines", filter=order_line_q, distinct=True),
        )
    else:
        qs = qs.annotate(
            matched_items_count=Count("lines", distinct=True),
        )

    total_count = qs.count()

    matched_line_qs = (
        OrderLine.objects.select_related(
            "variant__product__brand",
            "offer__warehouse",
        )
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
        .order_by("id")
    )
    if has_line_filters:
        matched_line_qs = matched_line_qs.filter(line_q)

    rows = list(
        qs.prefetch_related(
            Prefetch("lines", queryset=matched_line_qs, to_attr="ops_matched_lines")
        )
        .order_by("-created_at", "-id")[offset : offset + page_size]
    )

    return OpsOrderSearchOut(
        page=page,
        page_size=page_size,
        total_count=total_count,
        results=[_serialize_ops_order(row) for row in rows],
    )


@router.get("/orders/{order_id}", response=OpsOrderDetailOut, auth=_auth)
def ops_order_detail(request, order_id: int):
    _ensure_shop_context_if_strict(request)
    _require_ops_user(request)
    shop_id = _request_shop_id(request)

    order = _get_order_for_ops(order_id, shop_id=shop_id)
    return _serialize_ops_order_detail(_order_detail_qs(order.id))


@router.post("/orders/{order_id}/actions/confirm", response=OpsActionResultOut, auth=_auth)
def ops_order_confirm(request, order_id: int, payload: OpsOrderActionIn):
    _ensure_shop_context_if_strict(request)
    user = _require_ops_user(request)
    order = _get_order_for_ops(order_id, shop_id=_request_shop_id(request))
    _apply_order_action(order, action="confirm", user=user, note=payload.note)
    return OpsActionResultOut(ok=True, message="Order confirmed", updated_ids=[order.id], failed_ids=[])


@router.post("/orders/{order_id}/actions/assign", response=OpsActionResultOut, auth=_auth)
def ops_order_assign(request, order_id: int, payload: OpsOrderActionIn):
    _ensure_shop_context_if_strict(request)
    user = _require_ops_user(request)
    order = _get_order_for_ops(order_id, shop_id=_request_shop_id(request))
    _apply_order_action(order, action="assign", user=user, assignee=payload.assignee, note=payload.note)
    return OpsActionResultOut(ok=True, message="Order assigned", updated_ids=[order.id], failed_ids=[])


@router.post("/orders/{order_id}/actions/move-to-picking", response=OpsActionResultOut, auth=_auth)
def ops_order_move_to_picking(request, order_id: int, payload: OpsOrderActionIn):
    _ensure_shop_context_if_strict(request)
    user = _require_ops_user(request)
    order = _get_order_for_ops(order_id, shop_id=_request_shop_id(request))
    _apply_order_action(order, action="move_to_picking", user=user, note=payload.note)
    return OpsActionResultOut(ok=True, message="Order moved to picking", updated_ids=[order.id], failed_ids=[])


@router.post("/orders/actions/bulk", response=OpsJobOut, auth=_auth)
def ops_orders_bulk_actions(request, payload: OpsOrderBulkActionIn):
    _ensure_shop_context_if_strict(request)
    user = _require_ops_user(request)
    shop = getattr(request, "shop", None)
    shop_id = int(shop.id) if shop is not None else None

    job = OpsJob.objects.create(
        job_type=OpsJob.JobType.ORDERS_BULK_ACTION,
        status=OpsJob.Status.QUEUED,
        shop_id=shop_id,
        actor_user=user,
        request_payload={
            "action": payload.action,
            "selected_ids": payload.selected_ids,
            "assignee": payload.assignee,
            "note": payload.note,
        },
    )
    from checkout.tasks import execute_ops_job_task
    try:
        execute_ops_job_task.delay(job.id)
    except Exception:
        # Broker unavailable: execute inline so the job is not silently dropped.
        _execute_ops_job(job, user=user)
    job.refresh_from_db()
    return _serialize_job(job)


@router.patch("/orders/{order_id}/shipping", response=OpsOrderShippingUpdateOut, auth=_auth)
def ops_update_order_shipping(request, order_id: int, payload: OpsOrderShippingUpdateIn):
    """Change carrier method and/or pickup locker on an order.

    A method/locker change always creates a NEW outbound shipment (the
    previous pending/packed one is cancelled) so an old label/tracking is
    never silently reused. Recalculates shipping price; for paid orders the
    delta automatically becomes a pending surcharge/refund row.
    """
    _ensure_shop_context_if_strict(request)
    user = _require_ops_user(request)
    order = _get_order_for_ops(order_id, shop_id=_request_shop_id(request))

    old_gross = Decimal(order.shipping_gross or 0)
    old_method = order.shipping_method or ""
    old_locker_id = order.pickup_locker_id

    if payload.shipping_method is not None:
        order.shipping_method = payload.shipping_method.strip()

    locker = None
    if payload.pickup_locker_id:
        from dpd.models import DpdLocker
        locker = DpdLocker.objects.filter(id=payload.pickup_locker_id).first()
        if locker is None:
            raise HttpError(404, "Locker not found")

    if locker is not None:
        order.pickup_locker = locker
        order.pickup_point_id = str(getattr(locker, "locker_id", "") or "")
        order.pickup_point_name = str(getattr(locker, "name", "") or "")
        order.pickup_point_raw = {
            "id": locker.id,
            "parcel_shop_id": order.pickup_point_id,
            "name": order.pickup_point_name,
            "country_code": getattr(locker, "country_code", ""),
        }
    elif payload.pickup_locker_id is None and payload.shipping_method and \
            payload.shipping_method.strip() not in {"dpd_locker", "unisend_pickup"}:
        # Switching to courier clears the locker selection.
        order.pickup_locker = None
        order.pickup_point_id = ""
        order.pickup_point_name = ""
        order.pickup_point_raw = {}

    method_changed = (order.shipping_method or "") != old_method
    locker_changed = locker is not None and locker.id != old_locker_id

    order.save(update_fields=[
        "shipping_method", "pickup_locker", "pickup_point_id",
        "pickup_point_name", "pickup_point_raw", "updated_at",
    ])
    order.recalculate_totals()
    order.save(update_fields=[
        "shipping_net", "shipping_vat", "shipping_gross",
        "total_net", "total_vat", "total_gross", "updated_at",
    ])

    new_shipment = None
    if method_changed or locker_changed:
        new_shipment = _replace_shipment_for_carrier_change(
            order=order,
            shipping_method=order.shipping_method,
            pickup_locker=order.pickup_locker,
            pickup_point_id=order.pickup_point_id,
            pickup_point_name=order.pickup_point_name,
            user=user,
        )

    new_gross = Decimal(order.shipping_gross or 0)
    delta = (new_gross - old_gross).quantize(Decimal("0.01"))

    from checkout.surcharges import balance_due, reconcile_order_balance
    reconcile_order_balance(
        order=order,
        reason=f"Shipping changed: {old_method or '-'} -> {order.shipping_method or '-'}",
        user=user,
    )

    _track_order_event(
        order=order,
        user=user,
        kind=OrderEvent.Kind.DELIVERY,
        name="ops_shipping_updated",
        payload={
            "old_shipping_method": old_method,
            "shipping_method": order.shipping_method,
            "old_shipping_gross": str(old_gross),
            "shipping_gross": str(new_gross),
            "price_delta_gross": str(delta),
            "pickup_point_id": order.pickup_point_id or "",
            "new_shipment_id": new_shipment.id if new_shipment else None,
        },
    )

    return OpsOrderShippingUpdateOut(
        ok=True,
        message="Shipping updated",
        order_id=order.id,
        shipping_method=order.shipping_method or "",
        shipping_gross=str(new_gross),
        price_delta_gross=str(delta),
        pickup_point_name=order.pickup_point_name or "",
        new_shipment_id=new_shipment.id if new_shipment else None,
        balance_due=str(balance_due(order=order)),
    )


@router.get("/orders/{order_id}/addresses", response=list[OpsCustomerAddressOut], auth=_auth)
def ops_order_customer_addresses(request, order_id: int):
    """Saved addresses of the order's customer (for the address picker)."""
    _ensure_shop_context_if_strict(request)
    _require_ops_user(request)
    order = _get_order_for_ops(order_id, shop_id=_request_shop_id(request))
    if not order.user_id:
        return []
    return [
        OpsCustomerAddressOut(
            id=int(a.id),
            label=(a.label or ""),
            full_name=(a.full_name or ""),
            company=(a.company or ""),
            company_reg_no=(a.company_reg_no or ""),
            company_vat_no=(a.company_vat_no or ""),
            line1=(a.line1 or ""),
            city=(a.city or ""),
            postal_code=(a.postal_code or ""),
            country_code=(a.country_code or ""),
            phone=(a.phone or ""),
            is_default_shipping=bool(a.is_default_shipping),
            is_default_billing=bool(a.is_default_billing),
        )
        for a in order.user.addresses.all()
    ]


@router.patch("/orders/{order_id}/address", response=OpsOrderDetailOut, auth=_auth)
def ops_update_order_address(request, order_id: int,
                             payload: OpsOrderAddressUpdateIn):
    """Set the order's shipping address — copy a saved customer address or
    enter a one-off address (optionally saved to the customer profile)."""
    _ensure_shop_context_if_strict(request)
    user = _require_ops_user(request)
    order = _get_order_for_ops(order_id, shop_id=_request_shop_id(request))

    if payload.user_address_id:
        addr = None
        if order.user_id:
            addr = order.user.addresses.filter(
                id=payload.user_address_id).first()
        if addr is None:
            raise HttpError(404, "Address not found for this customer")
        order.shipping_full_name = addr.full_name or order.shipping_full_name
        order.shipping_company = addr.company or ""
        order.shipping_line1 = addr.line1
        order.shipping_city = addr.city
        order.shipping_postal_code = addr.postal_code
        order.shipping_country_code = addr.country_code or order.shipping_country_code
        if addr.phone:
            order.shipping_phone = addr.phone
        if not order.country_code:
            order.country_code = addr.country_code
    else:
        required = {
            "line1": payload.line1.strip(),
            "city": payload.city.strip(),
            "postal_code": payload.postal_code.strip(),
            "country_code": payload.country_code.strip(),
        }
        missing = [k for k, v in required.items() if not v]
        if missing:
            raise HttpError(400, f"Missing address fields: {', '.join(missing)}")
        order.shipping_full_name = payload.full_name.strip() or order.shipping_full_name
        order.shipping_company = payload.company.strip()
        order.shipping_line1 = required["line1"]
        order.shipping_city = required["city"]
        order.shipping_postal_code = required["postal_code"]
        order.shipping_country_code = required["country_code"].upper()
        if payload.phone.strip():
            order.shipping_phone = payload.phone.strip()
        if not order.country_code:
            order.country_code = required["country_code"].upper()

        if payload.save_to_customer and order.user_id:
            from accounts.models import UserAddress
            has_default = order.user.addresses.filter(
                is_default_shipping=True).exists()
            if payload.set_default_shipping and has_default:
                order.user.addresses.filter(
                    is_default_shipping=True).update(
                        is_default_shipping=False)
                has_default = False
            UserAddress.objects.create(
                user=order.user,
                label="Iš užsakymo",
                full_name=order.shipping_full_name,
                company=order.shipping_company,
                line1=order.shipping_line1,
                city=order.shipping_city,
                postal_code=order.shipping_postal_code,
                country_code=order.shipping_country_code or "LT",
                phone=order.shipping_phone,
                is_default_shipping=payload.set_default_shipping
                or not has_default,
            )

    order.save(update_fields=[
        "shipping_full_name", "shipping_company", "shipping_line1",
        "shipping_city", "shipping_postal_code", "shipping_country_code",
        "shipping_phone", "country_code", "updated_at",
    ])

    _track_order_event(
        order=order,
        user=user,
        kind=OrderEvent.Kind.NOTE,
        name="ops_address_updated",
        payload={
            "user_address_id": payload.user_address_id,
            "line1": order.shipping_line1,
            "city": order.shipping_city,
            "country_code": order.shipping_country_code,
        },
    )
    return _serialize_ops_order_detail(_order_detail_qs(order.id))


@router.patch("/orders/{order_id}/supplier", response=OpsOrderDetailOut, auth=_auth)
def ops_update_order_supplier(request, order_id: int,
                              payload: OpsOrderSupplierUpdateIn):
    """Manual supplier-reservation tracking: status (pending when the supplier
    is being contacted, reserved when confirmed, failed/cancelled),
    supplier reference and internal notes."""
    _ensure_shop_context_if_strict(request)
    user = _require_ops_user(request)
    order = _get_order_for_ops(order_id, shop_id=_request_shop_id(request))

    changed: list[str] = []
    if payload.supplier_reservation_status is not None:
        status = payload.supplier_reservation_status.strip()
        valid = {c.value for c in Order.SupplierReservationStatus}
        if status not in valid:
            raise HttpError(400, f"Invalid supplier_reservation_status '{status}'")
        order.supplier_reservation_status = status
        now = timezone.now()
        if status == Order.SupplierReservationStatus.RESERVED:
            if order.supplier_reserved_at is None:
                order.supplier_reserved_at = now
        else:
            order.supplier_reserved_at = None
        changed.extend(["supplier_reservation_status", "supplier_reserved_at"])
    if payload.supplier_reference is not None:
        order.supplier_reference = payload.supplier_reference.strip()
        changed.append("supplier_reference")
    if payload.supplier_notes is not None:
        order.supplier_notes = payload.supplier_notes
        changed.append("supplier_notes")

    if not changed:
        raise HttpError(400, "No fields to update")
    order.save(update_fields=changed + ["updated_at"])

    _track_order_event(
        order=order,
        user=user,
        kind=OrderEvent.Kind.FULFILLMENT,
        name="ops_supplier_updated",
        payload={
            "fields": changed,
            "supplier_reservation_status": order.supplier_reservation_status,
            "supplier_reference": order.supplier_reference,
        },
    )
    return _serialize_ops_order_detail(_order_detail_qs(order.id))


def _surcharge_to_out(s: Surcharge) -> OpsSurchargeOut:
    return OpsSurchargeOut(
        id=int(s.id),
        order_id=int(s.order_id),
        direction=s.direction,
        source=s.source,
        amount_gross=str(s.amount_gross),
        currency=s.currency,
        method=s.method or "",
        status=s.status,
        reason=s.reason or "",
        payment_url=s.payment_url or "",
        emailed_at=_to_iso(s.emailed_at),
        paid_at=_to_iso(s.paid_at),
        created_at=_to_iso(s.created_at),
    )


def _add_order_line(*, order: Order, variant, qty: int, offer=None, user=None) -> OrderLine:
    from catalog.models import Variant
    from checkout.api.shared import resolve_shop_variant_price
    from checkout.services import get_vat_rate_for, money_from_net

    country = (order.country_code or "LT").upper()
    priced = resolve_shop_variant_price(
        variant=variant,
        shop_id=(order.shop_id if order.shop_id else None),
        channel_type="retail",
    )
    unit_net = getattr(priced, "unit_net", priced)
    if unit_net is None:
        raise HttpError(400, "Variant has no price in this shop")
    vat_rate = get_vat_rate_for(
        country_code=country,
        tax_class=variant.product.tax_class if variant.product_id else None)
    unit = money_from_net(
        currency=order.currency or "EUR", unit_net=unit_net,
        vat_rate=vat_rate, qty=1)
    m = money_from_net(
        currency=order.currency or "EUR", unit_net=unit_net,
        vat_rate=vat_rate, qty=qty)

    line = OrderLine.objects.create(
        order=order,
        offer=offer,
        variant=variant,
        sku=variant.sku or "",
        name=variant.product.name if variant.product_id else (variant.sku or ""),
        unit_net=unit.net,
        vat_rate=vat_rate,
        unit_vat=unit.vat,
        unit_gross=unit.gross,
        qty=qty,
        total_net=m.net,
        total_vat=m.vat,
        total_gross=m.gross,
    )
    order.recalculate_totals()
    order.save(update_fields=[
        "items_net", "items_vat", "items_gross",
        "shipping_net", "shipping_vat", "shipping_gross",
        "total_net", "total_vat", "total_gross", "updated_at",
    ])
    return line


@router.post("/orders/{order_id}/lines", response=OpsOrderDetailOut, auth=_auth)
def ops_add_order_line(request, order_id: int, payload: OpsOrderLineIn):
    """Add a product line to an existing order (admin order editing)."""
    _ensure_shop_context_if_strict(request)
    user = _require_ops_user(request)
    order = _get_order_for_ops(order_id, shop_id=_request_shop_id(request))

    from catalog.models import Variant
    variant = Variant.objects.select_related("product", "product__tax_class").filter(
        id=payload.variant_id).first()
    if variant is None:
        raise HttpError(404, "Variant not found")
    if payload.qty < 1:
        raise HttpError(400, "qty must be >= 1")

    offer = None
    if payload.offer_id:
        from catalog.models import InventoryItem
        offer = InventoryItem.objects.filter(id=payload.offer_id).first()
        if offer is None:
            raise HttpError(404, "Offer not found")

    with transaction.atomic():
        line = _add_order_line(
            order=order, variant=variant, qty=payload.qty, offer=offer, user=user)

        from checkout.services import reserve_inventory_for_line
        try:
            allocated = reserve_inventory_for_line(
                order_line=line, qty=payload.qty, strict=bool(offer))
        except ValueError as exc:
            raise HttpError(409, str(exc) or "Not enough stock")

    _track_order_event(
        order=order,
        user=user,
        kind=OrderEvent.Kind.NOTE,
        name="ops_line_added",
        payload={
            "order_line_id": line.id,
            "variant_id": variant.id,
            "qty": payload.qty,
            "allocated_qty": int(allocated),
            "total_gross": str(line.total_gross),
        },
    )

    from checkout.surcharges import reconcile_order_balance
    reconcile_order_balance(
        order=order,
        reason=f"Line added: {line.name} x{payload.qty}",
        user=user,
    )

    return _serialize_ops_order_detail(_order_detail_qs(order.id))


def _net_for_unit_gross(unit_gross: Decimal, vat_rate: Decimal) -> Decimal:
    """Pick a unit_net whose computed unit_gross matches the target exactly."""
    from pricing.services import compute_vat

    unit_gross = Decimal(unit_gross)
    if unit_gross <= 0:
        return Decimal("0.00")
    rate = Decimal(vat_rate or 0)
    base = (unit_gross / (Decimal("1") + rate)).quantize(Decimal("0.01"))
    for cents in range(0, 5):
        for cand in (base + Decimal("0.01") * cents,
                     base - Decimal("0.01") * cents):
            if cand <= 0:
                continue
            if compute_vat(unit_net=cand, vat_rate=rate,
                           qty=1).unit_gross == unit_gross:
                return cand
    return base


@router.patch("/orders/{order_id}/lines/{line_id}", response=OpsOrderDetailOut, auth=_auth)
def ops_update_order_line(request, order_id: int, line_id: int,
                          payload: OpsOrderLineUpdateIn):
    """Edit a line: qty (0 removes), unit price (gross or net), name."""
    _ensure_shop_context_if_strict(request)
    user = _require_ops_user(request)
    order = _get_order_for_ops(order_id, shop_id=_request_shop_id(request))

    line = order.lines.filter(id=line_id).first()
    if line is None:
        raise HttpError(404, "Order line not found")

    if (payload.qty is None and payload.unit_gross is None
            and payload.unit_net is None and payload.name is None):
        raise HttpError(400, "Nothing to update")

    new_qty = payload.qty if payload.qty is not None else int(line.qty)
    if new_qty < 0:
        raise HttpError(400, "qty must be >= 0")

    new_unit_net = None
    old_unit_gross = Decimal(line.unit_gross or 0)
    if payload.unit_gross is not None:
        try:
            target_gross = Decimal(str(payload.unit_gross)).quantize(Decimal("0.01"))
        except Exception:
            raise HttpError(400, "Invalid unit_gross")
        if target_gross < 0:
            raise HttpError(400, "unit_gross must be >= 0")
        new_unit_net = _net_for_unit_gross(target_gross, line.vat_rate)
    elif payload.unit_net is not None:
        try:
            new_unit_net = Decimal(str(payload.unit_net)).quantize(Decimal("0.01"))
        except Exception:
            raise HttpError(400, "Invalid unit_net")
        if new_unit_net < 0:
            raise HttpError(400, "unit_net must be >= 0")

    new_name = None
    if payload.name is not None:
        new_name = payload.name.strip()
        if not new_name:
            raise HttpError(400, "name must not be empty")

    old_qty = int(line.qty)
    old_name = line.name
    with transaction.atomic():
        from checkout.services import (
            release_inventory_for_line,
            reserve_inventory_for_line,
        )
        if new_qty == 0:
            released = release_inventory_for_line(order_line=line)
            line.delete()
            removed = True
            allocated = 0
        else:
            delta = int(new_qty) - int(old_qty)
            released = 0
            allocated = 0
            if delta > 0:
                allocated = reserve_inventory_for_line(
                    order_line=line, qty=delta, strict=False)
            elif delta < 0:
                released = release_inventory_for_line(order_line=line, qty=-delta)
            if new_unit_net is not None:
                line.unit_net = new_unit_net
            if new_name is not None:
                line.name = new_name
            line.qty = new_qty
            line.save()  # recomputes unit_* and totals from unit_net+vat_rate+qty
            removed = False

    order.recalculate_totals()
    order.save(update_fields=[
        "items_net", "items_vat", "items_gross",
        "shipping_net", "shipping_vat", "shipping_gross",
        "total_net", "total_vat", "total_gross", "updated_at",
    ])

    if removed:
        event_name = "ops_line_removed"
        reason = f"Line removed: {old_name} x{old_qty}"
    else:
        event_name = "ops_line_updated"
        changes = []
        if new_qty != old_qty:
            changes.append(f"qty {old_qty} -> {new_qty}")
        if new_unit_net is not None:
            line.refresh_from_db()
            changes.append(
                f"unit {old_unit_gross} -> {line.unit_gross} gross")
        if new_name is not None and new_name != old_name:
            changes.append("name")
        reason = f"Line updated: {line.name} ({', '.join(changes) or 'no change'})"

    _track_order_event(
        order=order,
        user=user,
        kind=OrderEvent.Kind.NOTE,
        name=event_name,
        payload={
            "order_line_id": line_id,
            "product_name": old_name,
            "old_qty": old_qty,
            "new_qty": new_qty,
            "allocated_qty": allocated,
            "released_qty": released,
        },
    )

    from checkout.surcharges import reconcile_order_balance
    reconcile_order_balance(order=order, reason=reason, user=user)

    return _serialize_ops_order_detail(_order_detail_qs(order.id))


_ORDER_TOTAL_FIELDS = [
    "items_net", "items_vat", "items_gross",
    "shipping_net", "shipping_vat", "shipping_gross",
    "total_net", "total_vat", "total_gross", "updated_at",
]


@router.post("/orders/{order_id}/discount", response=OpsOrderDetailOut, auth=_auth)
def ops_add_order_discount(request, order_id: int, payload: OpsOrderDiscountIn):
    """Apply a discount to an order: percent/amount (manual) or an existing
    coupon code. Manual discounts apply to item totals (like coupons)."""
    _ensure_shop_context_if_strict(request)
    user = _require_ops_user(request)
    order = _get_order_for_ops(order_id, shop_id=_request_shop_id(request))

    kind = (payload.kind or "").strip().lower()
    if kind not in {"percent", "amount", "coupon"}:
        raise HttpError(400, "kind must be percent | amount | coupon")

    with transaction.atomic():
        locked = Order.objects.select_for_update().get(id=order.id)
        locked.recalculate_totals()
        items_net = Decimal(locked.items_net or 0)
        items_vat = Decimal(locked.items_vat or 0)
        items_gross = Decimal(locked.items_gross or 0)
        eff_rate = (items_vat / items_net) if items_net > 0 else Decimal("0")
        existing_net = sum(
            (Decimal(d.net) for d in locked.discounts.all()), Decimal("0.00"))
        existing_gross = sum(
            (Decimal(d.gross) for d in locked.discounts.all()), Decimal("0.00"))

        if kind == "coupon":
            code = (payload.code or "").strip().lower()
            if not code:
                raise HttpError(400, "code is required")
            if locked.discounts.filter(kind="coupon").exists():
                raise HttpError(409, "Užsakymui jau pritaikytas kuponas")
            from promotions.models import Coupon
            coupon = Coupon.objects.filter(code=code).first()
            if coupon is None or not coupon.is_valid_now():
                raise HttpError(400, "Kuponas neegzistuoja arba nebegalioja")

            eligible_net = Decimal("0.00")
            for ln in locked.lines.select_related("offer").all():
                if ln.offer_id and getattr(ln.offer, "never_discount", False):
                    continue
                eligible_net += Decimal(ln.total_net or 0)

            net = coupon.get_discount_net_for(eligible_items_net=eligible_net)
            net = min(net, max(Decimal("0.00"), eligible_net - existing_net))
            free_ship = coupon.is_free_shipping_for(
                shipping_method=locked.shipping_method)
            if net <= 0 and not free_ship:
                raise HttpError(400, "Kuponas netaikomas šioms prekėms")
            vat = (net * eff_rate).quantize(Decimal("0.01"))
            disc = OrderDiscount.objects.create(
                order=locked, kind="coupon", code=code,
                name=(coupon.name or code),
                net=net, vat=vat, gross=net + vat)
            if free_ship and Decimal(locked.shipping_gross or 0) > 0:
                locked.shipping_net_manual = Decimal("0.00")
                locked.save(update_fields=["shipping_net_manual"])
            if not reserve_coupon_for_order(order_id=locked.id):
                raise HttpError(409, "Kupono panaudojimų limitas pasiektas")
            label = f"Kuponas {code}"
        else:
            try:
                value = Decimal(str(payload.value or "")).quantize(Decimal("0.01"))
            except Exception:
                raise HttpError(400, "value is required")
            if kind == "percent":
                if value <= 0 or value > 100:
                    raise HttpError(400, "percent must be 0 < p <= 100")
                net = (items_net * value / Decimal("100")).quantize(Decimal("0.01"))
                net = min(net, max(Decimal("0.00"), items_net - existing_net))
                vat = (net * eff_rate).quantize(Decimal("0.01"))
                gross = net + vat
                default_name = f"Nuolaida {value}%"
            else:
                if value <= 0:
                    raise HttpError(400, "amount must be > 0")
                gross = min(value, max(Decimal("0.00"),
                                       items_gross - existing_gross))
                net = (gross / (Decimal("1") + eff_rate)).quantize(Decimal("0.01"))
                vat = gross - net
                default_name = "Nuolaida"
            if net <= 0:
                raise HttpError(400, "Discount would be zero")
            disc = OrderDiscount.objects.create(
                order=locked, kind="manual",
                name=(payload.name or "").strip() or default_name,
                net=net, vat=vat, gross=gross)
            label = disc.name

        locked.recalculate_totals()
        locked.save(update_fields=_ORDER_TOTAL_FIELDS)

    _track_order_event(
        order=locked, user=user, kind=OrderEvent.Kind.NOTE,
        name="ops_discount_added",
        payload={
            "discount_id": int(disc.id), "kind": disc.kind,
            "code": disc.code or "", "name": disc.name or "",
            "gross": str(disc.gross),
        },
    )

    from checkout.surcharges import reconcile_order_balance
    reconcile_order_balance(
        order=locked, reason=f"Discount applied: {label}", user=user)

    return _serialize_ops_order_detail(_order_detail_qs(order.id))


@router.post("/orders/{order_id}/discounts/{discount_id}/remove",
             response=OpsOrderDetailOut, auth=_auth)
def ops_remove_order_discount(request, order_id: int, discount_id: int):
    """Remove a discount row; releases the coupon reservation if it was one."""
    _ensure_shop_context_if_strict(request)
    user = _require_ops_user(request)
    order = _get_order_for_ops(order_id, shop_id=_request_shop_id(request))

    disc = order.discounts.filter(id=discount_id).first()
    if disc is None:
        raise HttpError(404, "Discount not found")

    label = disc.code or disc.name or f"#{disc.id}"
    with transaction.atomic():
        locked = Order.objects.select_for_update().get(id=order.id)
        if disc.kind == "coupon":
            release_coupon_for_order(order_id=locked.id)
            # Restore shipping if this coupon had zeroed it.
            from promotions.models import Coupon
            coupon = Coupon.objects.filter(
                code=(disc.code or "").strip().lower()).first()
            if (coupon is not None
                    and coupon.is_free_shipping_for(
                        shipping_method=locked.shipping_method)
                    and locked.shipping_net_manual == Decimal("0.00")):
                locked.shipping_net_manual = None
                locked.save(update_fields=["shipping_net_manual"])
        disc.delete()
        locked.recalculate_totals()
        locked.save(update_fields=_ORDER_TOTAL_FIELDS)

    _track_order_event(
        order=locked, user=user, kind=OrderEvent.Kind.NOTE,
        name="ops_discount_removed",
        payload={"discount_id": int(discount_id), "label": label},
    )

    from checkout.surcharges import reconcile_order_balance
    reconcile_order_balance(
        order=locked, reason=f"Discount removed: {label}", user=user)

    return _serialize_ops_order_detail(_order_detail_qs(order.id))


@router.get("/surcharges/{surcharge_id}", response=OpsSurchargeOut, auth=_auth)
def ops_get_surcharge(request, surcharge_id: int):
    _ensure_shop_context_if_strict(request)
    _require_ops_user(request)
    s = _get_surcharge_for_ops(surcharge_id, shop_id=_request_shop_id(request))
    return _surcharge_to_out(s)


@router.post("/surcharges/{surcharge_id}/collect", response=OpsSurchargeOut, auth=_auth)
def ops_collect_surcharge(request, surcharge_id: int,
                          payload: OpsSurchargeCollectIn):
    """Pick how a pending charge is collected: montonio payment link or
    bank-transfer instructions; optionally email the customer."""
    _ensure_shop_context_if_strict(request)
    user = _require_ops_user(request)
    s = _get_surcharge_for_ops(surcharge_id, shop_id=_request_shop_id(request))

    from checkout.surcharges import collect_surcharge
    try:
        s = collect_surcharge(
            surcharge=s,
            method=payload.method,
            language_code=payload.language_code,
            user=user,
            send_email=payload.send_email,
        )
    except ValueError as exc:
        raise HttpError(400, str(exc))
    except Exception as exc:
        raise HttpError(502, f"Surcharge collection failed: {exc}")
    return _surcharge_to_out(s)


@router.post("/surcharges/{surcharge_id}/mark-paid", response=OpsSurchargeOut, auth=_auth)
def ops_mark_surcharge_paid(request, surcharge_id: int):
    _ensure_shop_context_if_strict(request)
    user = _require_ops_user(request)
    s = _get_surcharge_for_ops(surcharge_id, shop_id=_request_shop_id(request))
    from checkout.surcharges import mark_surcharge_paid
    return _surcharge_to_out(mark_surcharge_paid(surcharge=s, user=user))


@router.post("/surcharges/{surcharge_id}/resolve", response=OpsSurchargeOut, auth=_auth)
def ops_resolve_surcharge(request, surcharge_id: int):
    """Resolve a pending refund row (money returned to the customer)."""
    _ensure_shop_context_if_strict(request)
    user = _require_ops_user(request)
    s = _get_surcharge_for_ops(surcharge_id, shop_id=_request_shop_id(request))
    from checkout.surcharges import resolve_refund
    try:
        return _surcharge_to_out(resolve_refund(surcharge=s, user=user))
    except ValueError as exc:
        raise HttpError(400, str(exc))


@router.post("/surcharges/{surcharge_id}/cancel", response=OpsSurchargeOut, auth=_auth)
def ops_cancel_surcharge(request, surcharge_id: int):
    _ensure_shop_context_if_strict(request)
    user = _require_ops_user(request)
    s = _get_surcharge_for_ops(surcharge_id, shop_id=_request_shop_id(request))
    from checkout.surcharges import cancel_surcharge
    return _surcharge_to_out(cancel_surcharge(surcharge=s, user=user))


@router.post("/orders/{order_id}/surcharges", response=OpsSurchargeOut, auth=_auth)
def ops_create_surcharge(request, order_id: int, payload: OpsSurchargeIn):
    """Create an additional charge and email it to the customer.

    method=montonio generates a payment link; method=bank_transfer uses the
    configured PaymentMethod instructions (no redeploy needed).
    """
    _ensure_shop_context_if_strict(request)
    user = _require_ops_user(request)
    order = _get_order_for_ops(order_id, shop_id=_request_shop_id(request))

    from checkout.surcharges import create_surcharge

    try:
        s = create_surcharge(
            order=order,
            amount_gross=payload.amount_gross,
            reason=payload.reason,
            method=payload.method,
            language_code=payload.language_code,
            user=user,
            send_email=payload.send_email,
        )
    except ValueError as exc:
        raise HttpError(400, str(exc))
    except Exception as exc:
        raise HttpError(502, f"Surcharge failed: {exc}")

    return _surcharge_to_out(s)


@router.get("/orders/{order_id}/surcharges", response=list[OpsSurchargeOut], auth=_auth)
def ops_list_surcharges(request, order_id: int):
    _ensure_shop_context_if_strict(request)
    _require_ops_user(request)
    order = _get_order_for_ops(order_id, shop_id=_request_shop_id(request))
    return [_surcharge_to_out(s) for s in order.surcharges.all()]


@router.post("/orders", response=OpsOrderDetailOut, auth=_auth)
def ops_create_order(request, payload: OpsOrderCreateIn):
    """Admin order creation (phone/mail orders). Lines are priced from the
    shop price list; totals are recalculated via recalculate_totals."""
    _ensure_shop_context_if_strict(request)
    user = _require_ops_user(request)
    shop = getattr(request, "shop", None)

    if not payload.lines:
        raise HttpError(400, "lines are required")

    User = get_user_model()
    order_user = None
    if payload.user_id:
        order_user = User.objects.filter(id=payload.user_id).first()
    elif payload.user_email.strip():
        email = payload.user_email.strip().lower()
        order_user = User.objects.filter(email__iexact=email).first()
        if order_user is None:
            order_user = User(email=email)
            order_user.set_unusable_password()
            order_user.save()
    if order_user is None:
        raise HttpError(400, "user_id or user_email is required")

    from catalog.models import Variant
    from checkout.api.shared import resolve_shop_variant_price
    from checkout.services import get_vat_rate_for, money_from_net

    country = (payload.shipping_country_code or payload.country_code or "LT").upper()

    with transaction.atomic():
        order = Order.objects.create(
            user=order_user,
            shop=shop,
            status=payload.status or Order.Status.PENDING_PAYMENT,
            shipping_method=payload.shipping_method or "dpd_locker",
            shipping_full_name=payload.shipping_full_name,
            shipping_phone=payload.shipping_phone,
            shipping_line1=payload.shipping_line1,
            shipping_city=payload.shipping_city,
            shipping_postal_code=payload.shipping_postal_code,
            shipping_country_code=country,
            country_code=country,
            shipping_net_manual=(
                Decimal(payload.shipping_net_manual)
                if payload.shipping_net_manual else None
            ),
        )
        if payload.pickup_locker_id:
            from dpd.models import DpdLocker
            locker = DpdLocker.objects.filter(id=payload.pickup_locker_id).first()
            if locker is None:
                raise HttpError(404, "Locker not found")
            order.pickup_locker = locker
            order.pickup_point_id = str(getattr(locker, "locker_id", "") or "")
            order.pickup_point_name = str(getattr(locker, "name", "") or "")
            order.save(update_fields=[
                "pickup_locker", "pickup_point_id", "pickup_point_name"])

        created_lines = []
        for li in payload.lines:
            v = Variant.objects.select_related("product", "product__tax_class").filter(
                id=li.variant_id).first()
            if v is None:
                raise HttpError(404, f"Variant {li.variant_id} not found")
            qty = max(1, int(li.qty or 1))
            unit_net = resolve_shop_variant_price(
                variant=v,
                shop_id=(shop.id if shop else None),
                channel_type="retail",
            ).unit_net
            vat_rate = get_vat_rate_for(
                country_code=country, tax_class=v.product.tax_class)
            unit = money_from_net(currency="EUR", unit_net=unit_net, vat_rate=vat_rate, qty=1)
            m = money_from_net(currency="EUR", unit_net=unit_net, vat_rate=vat_rate, qty=qty)
            created_lines.append(OrderLine.objects.create(
                order=order,
                variant=v,
                sku=v.sku,
                name=v.product.name if v.product_id else v.sku,
                unit_net=unit.net,
                vat_rate=vat_rate,
                unit_vat=unit.vat,
                unit_gross=unit.gross,
                qty=qty,
                total_net=m.net,
                total_vat=m.vat,
                total_gross=m.gross,
            ))

        order.recalculate_totals()
        order.save()

        from checkout.services import reserve_inventory_for_line
        unallocated = []
        for line in created_lines:
            got = reserve_inventory_for_line(
                order_line=line, qty=int(line.qty), strict=False)
            if int(got) < int(line.qty):
                unallocated.append({
                    "order_line_id": int(line.id),
                    "sku": line.sku,
                    "missing": int(line.qty) - int(got),
                })

        if payload.note:
            OrderEvent.objects.create(
                order=order,
                kind=OrderEvent.Kind.NOTE,
                name="ops_order_note",
                actor_user=user,
                payload={"note": payload.note},
            )
        OrderEvent.objects.create(
            order=order,
            kind=OrderEvent.Kind.NOTE,
            name="ops_order_created",
            actor_user=user,
            payload={
                "user": getattr(user, "email", "") or "",
                "unallocated_lines": unallocated,
            },
        )

    return _serialize_ops_order_detail(_order_detail_qs(order.id))


@router.post("/orders/actions/merge", response=OpsActionResultOut, auth=_auth)
def ops_merge_orders(request, payload: OpsOrderMergeIn):
    """Merge orders into the primary one. Lines, fees, discounts, payments,
    shipments and events move to the primary; merged orders are cancelled."""
    _ensure_shop_context_if_strict(request)
    user = _require_ops_user(request)
    shop_id = _request_shop_id(request)

    primary = _get_order_for_ops(payload.primary_order_id, shop_id=shop_id)
    if not payload.merged_order_ids:
        raise HttpError(400, "merged_order_ids are required")

    merged_ids = [int(i) for i in payload.merged_order_ids if int(i) != primary.id]
    if not merged_ids:
        raise HttpError(400, "merged_order_ids must differ from primary_order_id")

    merged = []
    with transaction.atomic():
        for oid in merged_ids:
            src = _get_order_for_ops(oid, shop_id=shop_id)
            if src.id == primary.id:
                continue
            if src.status in {Order.Status.CANCELLED}:
                raise HttpError(409, f"Order {oid} cannot be merged (status={src.status})")

            src.lines.update(order=primary)
            src.fees.update(order=primary)
            src.discounts.update(order=primary)
            src.shipments.update(order=primary)
            src.surcharges.update(order=primary)
            InventoryAllocation.objects.filter(order=src).update(order=primary)
            PaymentIntent.objects.filter(order=src).update(order=primary)
            src.events.update(order=primary)

            src.status = Order.Status.CANCELLED
            src.save(update_fields=["status", "updated_at"])
            OrderEvent.objects.create(
                order=src,
                kind=OrderEvent.Kind.NOTE,
                name="ops_order_merged_into",
                actor_user=user,
                payload={"merged_into_order_id": primary.id},
            )
            merged.append(src.id)

        primary.recalculate_totals()
        primary.save()
        OrderEvent.objects.create(
            order=primary,
            kind=OrderEvent.Kind.NOTE,
            name="ops_orders_merged",
            actor_user=user,
            payload={"merged_order_ids": merged},
        )
        # Supplier reservation status is a manual workflow flag — merging
        # orders does not change it (the manager sets it by hand).

    from checkout.surcharges import reconcile_order_balance
    reconcile_order_balance(
        order=primary,
        reason=f"Orders merged: {merged}",
        user=user,
    )

    return OpsActionResultOut(
        ok=True,
        message=f"Merged {len(merged)} order(s) into {primary.id}",
        updated_ids=[primary.id],
        failed_ids=[],
    )
