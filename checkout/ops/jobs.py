from __future__ import annotations

from .shared import *


@router.get("/audit/events", response=OpsAuditEventsOut, auth=_auth)
def ops_audit_events(
    request,
    order_id: int | None = None,
    kind: str = "",
    name: str = "",
    page: int = 1,
    page_size: int = 50,
):
    _ensure_shop_context_if_strict(request)
    _require_ops_user(request)
    shop_id = _request_shop_id(request)
    page = max(1, int(page or 1))
    page_size = max(1, min(int(page_size or 50), 100))
    offset = (page - 1) * page_size

    qs = OrderEvent.objects.select_related("order")
    if shop_id is not None:
        qs = qs.filter(order__shop_id=shop_id)
    else:
        qs = qs.filter(order__shop__isnull=True)
    if order_id is not None:
        qs = qs.filter(order_id=order_id)
    if kind:
        qs = qs.filter(kind=kind)
    if name:
        qs = qs.filter(name=name)

    total_count = qs.count()
    rows = list(qs.order_by("-created_at", "-id")[offset : offset + page_size])
    out = [
        OpsAuditEventOut(
            id=int(ev.id),
            order_id=int(ev.order_id),
            kind=(ev.kind or ""),
            name=(ev.name or ""),
            actor_label=(ev.actor_label or ""),
            payload=dict(ev.payload or {}),
            created_at=_to_iso(ev.created_at),
        )
        for ev in rows
    ]
    return OpsAuditEventsOut(page=page, page_size=page_size, total_count=total_count, results=out)


@router.get("/jobs", response=OpsJobsListOut, auth=_auth)
def ops_jobs(
    request,
    status: str = "",
    job_type: str = "",
    page: int = 1,
    page_size: int = 50,
):
    _ensure_shop_context_if_strict(request)
    _require_ops_user(request)
    shop_id = _request_shop_id(request)
    page = max(1, int(page or 1))
    page_size = max(1, min(int(page_size or 50), 100))
    offset = (page - 1) * page_size

    qs = OpsJob.objects.all()
    if shop_id is not None:
        qs = qs.filter(shop_id=shop_id)
    else:
        qs = qs.filter(shop__isnull=True)
    if status:
        qs = qs.filter(status=status)
    if job_type:
        qs = qs.filter(job_type=job_type)

    total_count = qs.count()
    rows = list(qs.order_by("-created_at", "-id")[offset : offset + page_size])
    return OpsJobsListOut(
        page=page,
        page_size=page_size,
        total_count=total_count,
        results=[_serialize_job(j) for j in rows],
    )


@router.get("/jobs/{job_id}", response=OpsJobOut, auth=_auth)
def ops_job_detail(request, job_id: int):
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


@router.post("/jobs/{job_id}/retry", response=OpsJobOut, auth=_auth)
def ops_job_retry(request, job_id: int):
    _ensure_shop_context_if_strict(request)
    user = _require_ops_user(request)
    shop_id = _request_shop_id(request)
    qs = OpsJob.objects.filter(id=job_id)
    if shop_id is not None:
        qs = qs.filter(shop_id=shop_id)
    else:
        qs = qs.filter(shop__isnull=True)
    job = qs.first()
    if job is None:
        raise HttpError(404, "Job not found")
    _execute_ops_job(job, user=user)
    job.refresh_from_db()
    return _serialize_job(job)
