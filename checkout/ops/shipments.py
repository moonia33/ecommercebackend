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


@router.get("/shipments/registry", response=OpsShipmentRegistryOut, auth=_auth)
def ops_shipment_registry(
    request,
    tracking: str = "",
    order_id: int | None = None,
    kind: str = "",
    status: str = "",
    carrier_code: str = "",
    shipping_method: str = "",
    page: int = 1,
    page_size: int = 50,
):
    """Shipment registry: the source of truth for parcels — one row per
    Shipment (outbound and return), not per order."""
    _ensure_shop_context_if_strict(request)
    _require_ops_user(request)
    shop_id = _request_shop_id(request)
    page = max(1, int(page or 1))
    page_size = max(1, min(int(page_size or 50), 200))
    offset = (page - 1) * page_size

    qs = Shipment.objects.select_related("order", "pickup_locker")
    if shop_id is not None:
        qs = qs.filter(order__shop_id=shop_id)
    else:
        qs = qs.filter(order__shop__isnull=True)
    if tracking.strip():
        qs = qs.filter(tracking_number__icontains=tracking.strip())
    if order_id:
        qs = qs.filter(order_id=int(order_id))
    if kind.strip():
        qs = qs.filter(kind=kind.strip())
    if status.strip():
        qs = qs.filter(status=status.strip())
    if carrier_code.strip():
        qs = qs.filter(carrier_code=carrier_code.strip())
    if shipping_method.strip():
        qs = qs.filter(shipping_method=shipping_method.strip())

    total_count = qs.count()
    rows = qs.order_by("-created_at", "-id")[offset : offset + page_size]
    return OpsShipmentRegistryOut(
        page=page,
        page_size=page_size,
        total_count=total_count,
        results=[_serialize_shipment(s) for s in rows],
    )


@router.post("/orders/{order_id}/shipments", response=OpsShipmentRowOut, auth=_auth)
def ops_create_shipment(request, order_id: int, payload: OpsShipmentCreateIn):
    """Register a new shipment on an order (extra parcel, corrected locker,
    manual carrier record, or a return-shipment record)."""
    _ensure_shop_context_if_strict(request)
    user = _require_ops_user(request)
    order = _get_order_for_ops(order_id, shop_id=_request_shop_id(request))

    kind = (payload.kind or "outbound").strip()
    if kind not in {Shipment.Kind.OUTBOUND, Shipment.Kind.RETURN}:
        raise HttpError(400, "kind must be outbound|return")

    shipping_method = (
        payload.shipping_method or order.shipping_method or ""
    ).strip()

    locker = None
    if payload.pickup_locker_id:
        from dpd.models import DpdLocker
        locker = DpdLocker.objects.filter(id=payload.pickup_locker_id).first()
        if locker is None:
            raise HttpError(404, "Locker not found")

    shipment = Shipment.objects.create(
        order=order,
        kind=kind,
        status=Shipment.Status.PENDING,
        shipping_method=shipping_method,
        carrier_code=(payload.carrier_code or "").strip(),
        tracking_number=(payload.tracking_number or "").strip(),
        pickup_locker=locker,
        pickup_point_id=str(getattr(locker, "locker_id", "") or ""),
        pickup_point_name=str(getattr(locker, "name", "") or ""),
        notes=payload.notes or "",
        created_by=user,
    )
    shipment.apply_to_order_projection()

    _track_order_event(
        order=order,
        user=user,
        kind=OrderEvent.Kind.DELIVERY,
        name="ops_shipment_created",
        payload={
            "shipment_id": shipment.id,
            "kind": kind,
            "tracking_number": shipment.tracking_number,
        },
    )
    return _serialize_shipment(shipment)


@router.patch("/shipments/{shipment_id}", response=OpsShipmentRowOut, auth=_auth)
def ops_update_shipment(request, shipment_id: int, payload: OpsShipmentUpdateIn):
    """Change carrier / method / locker / tracking / status on a shipment."""
    _ensure_shop_context_if_strict(request)
    user = _require_ops_user(request)
    shipment = _get_shipment_for_ops(shipment_id, shop_id=_request_shop_id(request))

    changed: list[str] = []
    if payload.shipping_method is not None:
        shipment.shipping_method = payload.shipping_method.strip()
        changed.append("shipping_method")
    if payload.carrier_code is not None:
        shipment.carrier_code = payload.carrier_code.strip()
        changed.append("carrier_code")
    if payload.tracking_number is not None:
        shipment.tracking_number = payload.tracking_number.strip()
        changed.append("tracking_number")
    if payload.carrier_shipment_id is not None:
        shipment.carrier_shipment_id = payload.carrier_shipment_id.strip()
        changed.append("carrier_shipment_id")
    if payload.status is not None:
        status = payload.status.strip()
        if status not in {c.value for c in Shipment.Status}:
            raise HttpError(400, f"Invalid status '{status}'")
        shipment.status = status
        changed.append("status")
    if payload.kind is not None:
        kind = payload.kind.strip()
        if kind not in {Shipment.Kind.OUTBOUND, Shipment.Kind.RETURN}:
            raise HttpError(400, "kind must be outbound|return")
        shipment.kind = kind
        changed.append("kind")
    if payload.pickup_locker_id is not None:
        if payload.pickup_locker_id:
            from dpd.models import DpdLocker
            locker = DpdLocker.objects.filter(id=payload.pickup_locker_id).first()
            if locker is None:
                raise HttpError(404, "Locker not found")
            shipment.pickup_locker = locker
            shipment.pickup_point_id = str(getattr(locker, "locker_id", "") or "")
            shipment.pickup_point_name = str(getattr(locker, "name", "") or "")
        else:
            shipment.pickup_locker = None
            shipment.pickup_point_id = ""
            shipment.pickup_point_name = ""
        changed.extend(["pickup_locker", "pickup_point_id", "pickup_point_name"])
    if payload.notes is not None:
        shipment.notes = payload.notes
        changed.append("notes")

    if not changed:
        raise HttpError(400, "No fields to update")
    shipment.save()
    shipment.apply_to_order_projection()

    _track_order_event(
        order=shipment.order,
        user=user,
        kind=OrderEvent.Kind.DELIVERY,
        name="ops_shipment_updated",
        payload={"shipment_id": shipment.id, "fields": changed},
    )
    return _serialize_shipment(shipment)


@router.post("/shipments/id/{shipment_id}/label", response=OpsActionResultOut, auth=_auth)
def ops_generate_label_for_shipment(request, shipment_id: int):
    _ensure_shop_context_if_strict(request)
    user = _require_ops_user(request)
    shipment = _get_shipment_for_ops(shipment_id, shop_id=_request_shop_id(request))
    shipment = _generate_shipment_label(shipment, user=user)
    return OpsActionResultOut(
        ok=True, message="Label generated",
        updated_ids=[shipment.id], failed_ids=[])


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


@router.get("/lockers", response=list[OpsLockerOptionOut], auth=_auth)
def ops_list_lockers(request, country_code: str = "", q: str = ""):
    """Locker directory for admin locker pickers."""
    _ensure_shop_context_if_strict(request)
    _require_ops_user(request)
    from dpd.models import DpdLocker

    qs = DpdLocker.objects.all()
    if country_code.strip():
        qs = qs.filter(country_code=country_code.strip().upper())
    if q.strip():
        term = q.strip()
        qs = qs.filter(
            Q(name__icontains=term)
            | Q(city__icontains=term)
            | Q(locker_id__icontains=term)
        )
    return [
        OpsLockerOptionOut(
            id=int(l.id),
            name=str(l.name or ""),
            country_code=str(l.country_code or ""),
            city=str(l.city or ""),
            address=str(getattr(l, "address", "") or getattr(l, "street", "") or ""),
        )
        for l in qs.order_by("country_code", "city", "name")[:100]
    ]


@router.post("/shipments/{order_id}/mark-shipped", response=OpsActionResultOut, auth=_auth)
def ops_mark_shipment_shipped(request, order_id: int):
    _ensure_shop_context_if_strict(request)
    user = _require_ops_user(request)
    order = _get_order_for_ops(order_id, shop_id=_request_shop_id(request))
    shipment = _get_or_create_primary_shipment(order, user=user)
    shipment.status = Shipment.Status.PICKED_UP
    shipment.save(update_fields=["status", "updated_at"])
    order.delivery_status = Order.DeliveryStatus.PICKED_UP
    order.save(update_fields=["delivery_status", "updated_at"])
    _track_order_event(
        order=order, user=user,
        kind=OrderEvent.Kind.DELIVERY, name="ops_mark_shipped",
        payload={"shipment_id": shipment.id})
    return OpsActionResultOut(
        ok=True, message="Shipment marked as shipped",
        updated_ids=[order.id], failed_ids=[])


@router.post("/shipments/{order_id}/refresh-tracking", response=OpsActionResultOut, auth=_auth)
def ops_refresh_tracking(request, order_id: int):
    _ensure_shop_context_if_strict(request)
    user = _require_ops_user(request)
    order = _get_order_for_ops(order_id, shop_id=_request_shop_id(request))
    shipment = _get_or_create_primary_shipment(order, user=user)
    _track_order_event(
        order=order,
        user=user,
        kind=OrderEvent.Kind.DELIVERY,
        name="ops_refresh_tracking",
        payload={
            "shipment_id": shipment.id,
            "carrier_code": shipment.carrier_code,
            "carrier_shipment_id": shipment.carrier_shipment_id,
            "tracking_number": shipment.tracking_number,
        },
    )
    return OpsActionResultOut(
        ok=True, message="Tracking refreshed",
        updated_ids=[order.id], failed_ids=[])
