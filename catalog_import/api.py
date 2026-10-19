from __future__ import annotations

import json

from ninja import Body, Query, Router
from ninja.errors import HttpError

from accounts.auth import JWTAuth
from checkout.ops.shared import _require_ops_user, _to_iso

from .models import ImportItem, ImportRun
from .schemas import ImportItemOut, ImportRunOut
from .services import _start_async_apply, create_run, execute_run

router = Router(tags=["ops-imports"])
_auth = JWTAuth()


def _serialize_run(run: ImportRun) -> ImportRunOut:
    return ImportRunOut(
        id=int(run.id),
        kind=run.kind,
        source=run.source,
        file_name=run.file_name,
        status=run.status,
        mode=run.mode,
        total_items=run.total_items,
        ok_items=run.ok_items,
        failed_items=run.failed_items,
        skipped_items=run.skipped_items,
        error_message=run.error_message or "",
        created_at=_to_iso(run.created_at),
        finished_at=_to_iso(run.finished_at),
    )


@router.post("/catalog", response=ImportRunOut, auth=_auth)
def import_catalog(
    request,
    payload: dict = Body(...),
    dry_run: bool = Query(False),
    apply_async: bool = Query(False),
):
    """Stage a normalized catalog import. Items validated per-row on apply."""
    user = _require_ops_user(request)
    body = json.dumps(payload).encode("utf-8")
    run = create_run(
        kind=ImportRun.Kind.CATALOG,
        payload_bytes=body,
        file_name=str(payload.get("file_name") or "api"),
        triggered_by=user,
        options={"dry_run": dry_run},
    )
    if run.status == ImportRun.Status.QUEUED:
        if apply_async:
            _start_async_apply(run)
        else:
            execute_run(run)
    return _serialize_run(run)


@router.post("/stock", response=ImportRunOut, auth=_auth)
def import_stock(
    request,
    payload: dict = Body(...),
    dry_run: bool = Query(False),
    apply_async: bool = Query(False),
):
    """Stage a stock import: {warehouse_code, items: [{code, qty}]}."""
    user = _require_ops_user(request)
    body = json.dumps(payload).encode("utf-8")
    run = create_run(
        kind=ImportRun.Kind.STOCK,
        payload_bytes=body,
        file_name=str(payload.get("file_name") or "api"),
        triggered_by=user,
        options={"dry_run": dry_run},
    )
    if run.status == ImportRun.Status.QUEUED:
        if apply_async:
            _start_async_apply(run)
        else:
            execute_run(run)
    return _serialize_run(run)


@router.post("/runs/{run_id}/apply", response=ImportRunOut, auth=_auth)
def apply_run(request, run_id: int, dry_run: bool = Query(False)):
    user = _require_ops_user(request)
    run = ImportRun.objects.filter(id=run_id).first()
    if run is None:
        raise HttpError(404, "Import run not found")
    if run.status not in (ImportRun.Status.QUEUED, ImportRun.Status.FAILED):
        raise HttpError(400, f"Run already {run.status}")
    if dry_run:
        opts = dict(run.options or {})
        opts["dry_run"] = True
        run.options = opts
        run.save(update_fields=["options"])
    execute_run(run, triggered_by=user)
    return _serialize_run(run)


@router.get("/categories", auth=_auth)
def list_import_categories(request):
    """Pilnas kategorijų medis normalyzeriui (mapinimo šaltinis).

    Skirtingai nuo storefront /catalog/categories – grąžina VISAS kategorijas,
    ne tik turinčias aktyvių prekių.
    """
    _require_ops_user(request)
    from catalog.models.taxonomy import Category

    return [
        {
            "id": c.id,
            "slug": c.slug,
            "name": c.name,
            "parent_slug": c.parent.slug if c.parent_id else "",
            "is_active": c.is_active,
        }
        for c in Category.objects.select_related("parent").order_by("id")
    ]


@router.get("/warehouses", auth=_auth)
def list_import_warehouses(request):
    """Warehouse kodai – likučių feed'o warehouse_code reikšmės."""
    _require_ops_user(request)
    from catalog.models.inventory import Warehouse

    return [
        {"id": w.id, "code": w.code, "name": w.name, "is_active": w.is_active}
        for w in Warehouse.objects.order_by("id")
    ]


@router.get("/runs", response=list[ImportRunOut], auth=_auth)
def list_runs(request, kind: str = "", status: str = "", limit: int = 50):
    _require_ops_user(request)
    qs = ImportRun.objects.order_by("-id")
    if kind:
        qs = qs.filter(kind=kind)
    if status:
        qs = qs.filter(status=status)
    return [_serialize_run(r) for r in qs[: min(limit, 200)]]


@router.get("/runs/{run_id}", response=ImportRunOut, auth=_auth)
def get_run(request, run_id: int):
    _require_ops_user(request)
    run = ImportRun.objects.filter(id=run_id).first()
    if run is None:
        raise HttpError(404, "Import run not found")
    return _serialize_run(run)


@router.get("/runs/{run_id}/items", response=list[ImportItemOut], auth=_auth)
def list_run_items(
    request, run_id: int, status: str = "", limit: int = 200, offset: int = 0
):
    _require_ops_user(request)
    qs = ImportItem.objects.filter(run_id=run_id).order_by("row_no")
    if status:
        qs = qs.filter(status=status)
    return [
        ImportItemOut(
            id=int(it.id),
            row_no=it.row_no,
            external_code=it.external_code or "",
            status=it.status,
            error=it.error or "",
            product_id=it.product_id,
            variant_id=it.variant_id,
        )
        for it in qs[offset : offset + min(limit, 500)]
    ]
