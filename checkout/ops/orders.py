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
        .prefetch_related("variant__product__images")
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
    order = (
        Order.objects.filter(id=order.id)
        .select_related("payment_intent", "shop", "user", "pickup_locker")
        .annotate(matched_items_count=Count("lines", distinct=True))
        .prefetch_related(
            Prefetch(
                "lines",
                queryset=OrderLine.objects.select_related("variant__product__brand", "offer__warehouse")
                .prefetch_related("variant__product__images")
                .order_by("id"),
                to_attr="ops_matched_lines",
            ),
            "events",
        )
        .first()
    )
    return _serialize_ops_order_detail(order)


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

    Recalculates shipping price and returns the gross delta — when positive,
    the admin can then create a surcharge (Montonio link / bank transfer).
    """
    _ensure_shop_context_if_strict(request)
    user = _require_ops_user(request)
    order = _get_order_for_ops(order_id, shop_id=_request_shop_id(request))

    old_gross = Decimal(order.shipping_gross or 0)
    old_method = order.shipping_method or ""

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
        order.pickup_point_id = str(getattr(locker, "parcel_shop_id", "") or "")
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

    order.save(update_fields=[
        "shipping_method", "pickup_locker", "pickup_point_id",
        "pickup_point_name", "pickup_point_raw", "updated_at",
    ])
    order.recalculate_totals()
    order.save(update_fields=[
        "shipping_net", "shipping_vat", "shipping_gross",
        "total_net", "total_vat", "total_gross", "updated_at",
    ])

    # Keep the primary outbound shipment in sync.
    if order.shipments.exists():
        primary = _get_or_create_primary_shipment(order, user=user)
        primary.shipping_method = order.shipping_method
        if locker is not None or order.pickup_locker is None:
            primary.pickup_locker = order.pickup_locker
            primary.pickup_point_id = order.pickup_point_id
            primary.pickup_point_name = order.pickup_point_name
        primary.save(update_fields=[
            "shipping_method", "pickup_locker",
            "pickup_point_id", "pickup_point_name", "updated_at",
        ])
        primary.apply_to_order_projection()

    new_gross = Decimal(order.shipping_gross or 0)
    delta = (new_gross - old_gross).quantize(Decimal("0.01"))

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
    )


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

    return OpsSurchargeOut(
        id=int(s.id),
        order_id=int(s.order_id),
        amount_gross=str(s.amount_gross),
        currency=s.currency,
        method=s.method,
        status=s.status,
        reason=s.reason or "",
        payment_url=s.payment_url or "",
        emailed_at=_to_iso(s.emailed_at),
        paid_at=_to_iso(s.paid_at),
        created_at=_to_iso(s.created_at),
    )


@router.get("/orders/{order_id}/surcharges", response=list[OpsSurchargeOut], auth=_auth)
def ops_list_surcharges(request, order_id: int):
    _ensure_shop_context_if_strict(request)
    _require_ops_user(request)
    order = _get_order_for_ops(order_id, shop_id=_request_shop_id(request))
    return [
        OpsSurchargeOut(
            id=int(s.id),
            order_id=int(s.order_id),
            amount_gross=str(s.amount_gross),
            currency=s.currency,
            method=s.method,
            status=s.status,
            reason=s.reason or "",
            payment_url=s.payment_url or "",
            emailed_at=_to_iso(s.emailed_at),
            paid_at=_to_iso(s.paid_at),
            created_at=_to_iso(s.created_at),
        )
        for s in order.surcharges.all()
    ]


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
            order.pickup_point_id = str(getattr(locker, "parcel_shop_id", "") or "")
            order.pickup_point_name = str(getattr(locker, "name", "") or "")
            order.save(update_fields=[
                "pickup_locker", "pickup_point_id", "pickup_point_name"])

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
            OrderLine.objects.create(
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
            )

        order.recalculate_totals()
        order.save()

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
            payload={"user": getattr(user, "email", "") or ""},
        )

    return _serialize_ops_order_detail(order)


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

    return OpsActionResultOut(
        ok=True,
        message=f"Merged {len(merged)} order(s) into {primary.id}",
        updated_ids=[primary.id],
        failed_ids=[],
    )
