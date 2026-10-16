from __future__ import annotations

from .shared import *


@router.get("/shipments", response=OpsShipmentListOut, auth=_auth)
def ops_list_shipments(
    request,
    status: str = "",
    shipping_method: str = "",
    page: int = 1,
    page_size: int = 50,
):
    _ensure_shop_context_if_strict(request)
    _require_ops_user(request)
    shop_id = _request_shop_id(request)
    page = max(1, int(page or 1))
    page_size = max(1, min(int(page_size or 50), 100))
    offset = (page - 1) * page_size

    qs = Order.objects.all()
    if shop_id is not None:
        qs = qs.filter(shop_id=shop_id)
    else:
        qs = qs.filter(shop__isnull=True)

    if status:
        qs = qs.filter(delivery_status=status)
    if shipping_method:
        qs = qs.filter(shipping_method=shipping_method)

    total_count = qs.count()
    rows = list(qs.order_by("-created_at", "-id")[offset : offset + page_size])

    out = [
        OpsShipmentOut(
            order_id=int(o.id),
            delivery_status=(o.delivery_status or ""),
            shipping_method=(o.shipping_method or ""),
            carrier_code=(o.carrier_code or ""),
            carrier_shipment_id=(o.carrier_shipment_id or ""),
            tracking_number=(o.tracking_number or ""),
            label_generated_at=_to_iso(o.shipping_label_generated_at),
        )
        for o in rows
    ]
    return OpsShipmentListOut(page=page, page_size=page_size, total_count=total_count, results=out)


@router.post("/shipments/{order_id}/label", response=OpsActionResultOut, auth=_auth)
def ops_generate_shipment_label(request, order_id: int):
    _ensure_shop_context_if_strict(request)
    user = _require_ops_user(request)
    order = _get_order_for_ops(order_id, shop_id=_request_shop_id(request))
    _generate_order_label(order, user=user)
    return OpsActionResultOut(ok=True, message="Label generated", updated_ids=[order.id], failed_ids=[])


@router.post("/shipments/labels/bulk", response=OpsJobOut, auth=_auth)
def ops_generate_shipment_labels_bulk(request, payload: OpsShipmentBulkLabelIn):
    _ensure_shop_context_if_strict(request)
    user = _require_ops_user(request)
    shop = getattr(request, "shop", None)
    shop_id = int(shop.id) if shop is not None else None

    job = OpsJob.objects.create(
        job_type=OpsJob.JobType.SHIPMENT_LABELS_BULK,
        status=OpsJob.Status.QUEUED,
        shop_id=shop_id,
        actor_user=user,
        request_payload={"order_ids": payload.order_ids},
    )
    _execute_ops_job(job, user=user)
    job.refresh_from_db()
    return _serialize_job(job)


@router.get("/shipments/labels/{job_id}", response=OpsJobOut, auth=_auth)
def ops_shipment_label_job(request, job_id: int):
    _ensure_shop_context_if_strict(request)
    _require_ops_user(request)
    shop_id = _request_shop_id(request)
    qs = OpsJob.objects.filter(id=job_id)
    if shop_id is not None:
        qs = qs.filter(shop_id=shop_id)
    else:
        qs = qs.filter(shop__isnull=True)
    job = qs.first()
    if job is None:
        raise HttpError(404, "Job not found")
    return _serialize_job(job)


@router.post("/shipments/{shipment_id}/mark-shipped", response=OpsActionResultOut, auth=_auth)
def ops_mark_shipment_shipped(request, shipment_id: int):
    _ensure_shop_context_if_strict(request)
    user = _require_ops_user(request)
    order = _get_order_for_ops(shipment_id, shop_id=_request_shop_id(request))
    order.delivery_status = Order.DeliveryStatus.PICKED_UP
    order.save(update_fields=["delivery_status", "updated_at"])
    _track_order_event(order=order, user=user, kind=OrderEvent.Kind.DELIVERY, name="ops_mark_shipped", payload={})
    return OpsActionResultOut(ok=True, message="Shipment marked as shipped", updated_ids=[order.id], failed_ids=[])


@router.post("/shipments/{shipment_id}/refresh-tracking", response=OpsActionResultOut, auth=_auth)
def ops_refresh_tracking(request, shipment_id: int):
    _ensure_shop_context_if_strict(request)
    user = _require_ops_user(request)
    order = _get_order_for_ops(shipment_id, shop_id=_request_shop_id(request))
    _track_order_event(
        order=order,
        user=user,
        kind=OrderEvent.Kind.DELIVERY,
        name="ops_refresh_tracking",
        payload={
            "carrier_code": order.carrier_code,
            "carrier_shipment_id": order.carrier_shipment_id,
            "tracking_number": order.tracking_number,
        },
    )
    return OpsActionResultOut(ok=True, message="Tracking refreshed", updated_ids=[order.id], failed_ids=[])
