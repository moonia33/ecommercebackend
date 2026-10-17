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
