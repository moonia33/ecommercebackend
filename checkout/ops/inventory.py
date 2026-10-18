from __future__ import annotations

from .shared import *


class OpsWarehouseOut(Schema):
    id: int
    code: str
    name: str = ""
    kind: str = ""
    country_code: str = ""


@router.get("/warehouses", response=list[OpsWarehouseOut], auth=_auth)
def ops_list_warehouses(request):
    _ensure_shop_context_if_strict(request)
    _require_ops_user(request)
    from catalog.models import Warehouse
    return [
        OpsWarehouseOut(
            id=int(w.id), code=w.code or "", name=w.name or "",
            kind=w.kind or "", country_code=w.country_code or "")
        for w in Warehouse.objects.filter(is_active=True)
        .order_by("sort_order", "code", "id")
    ]


@router.get("/inventory/alerts", response=OpsInventoryListOut, auth=_auth)
def ops_inventory_alerts(
    request,
    threshold: int = 0,
    page: int = 1,
    page_size: int = 50,
):
    _ensure_shop_context_if_strict(request)
    _require_ops_user(request)
    threshold = int(threshold or 0)
    page = max(1, int(page or 1))
    page_size = max(1, min(int(page_size or 50), 100))
    offset = (page - 1) * page_size

    qs = InventoryItem.objects.select_related("variant__product", "warehouse").filter(
        qty_on_hand__lte=threshold
    )
    total_count = qs.count()
    rows = list(qs.order_by("qty_on_hand", "id")[offset : offset + page_size])

    out = [
        OpsInventoryItemOut(
            id=int(i.id),
            sku=str(getattr(i.variant, "sku", "") or ""),
            product_name=str(getattr(getattr(i.variant, "product", None), "name", "") or ""),
            warehouse_code=str(getattr(i.warehouse, "code", "") or ""),
            qty_on_hand=int(i.qty_on_hand),
            qty_reserved=int(i.qty_reserved),
            qty_available=int(i.qty_available),
            offer_visibility=(i.offer_visibility or ""),
        )
        for i in rows
    ]
    return OpsInventoryListOut(page=page, page_size=page_size, total_count=total_count, results=out)


@router.get("/inventory/items", response=OpsInventoryListOut, auth=_auth)
def ops_inventory_items(
    request,
    sku: str = "",
    product_q: str = "",
    warehouse_code: str = "",
    page: int = 1,
    page_size: int = 50,
):
    _ensure_shop_context_if_strict(request)
    _require_ops_user(request)
    page = max(1, int(page or 1))
    page_size = max(1, min(int(page_size or 50), 100))
    offset = (page - 1) * page_size

    qs = InventoryItem.objects.select_related("variant__product", "warehouse")
    if sku:
        qs = qs.filter(variant__sku__icontains=sku)
    if product_q:
        qs = qs.filter(
            Q(variant__product__name__icontains=product_q)
            | Q(variant__product__slug__icontains=product_q)
        )
    if warehouse_code:
        qs = qs.filter(warehouse__code__iexact=warehouse_code)

    total_count = qs.count()
    rows = list(qs.order_by("id")[offset : offset + page_size])
    out = [
        OpsInventoryItemOut(
            id=int(i.id),
            sku=str(getattr(i.variant, "sku", "") or ""),
            product_name=str(getattr(getattr(i.variant, "product", None), "name", "") or ""),
            warehouse_code=str(getattr(i.warehouse, "code", "") or ""),
            qty_on_hand=int(i.qty_on_hand),
            qty_reserved=int(i.qty_reserved),
            qty_available=int(i.qty_available),
            offer_visibility=(i.offer_visibility or ""),
        )
        for i in rows
    ]
    return OpsInventoryListOut(page=page, page_size=page_size, total_count=total_count, results=out)


@router.post("/inventory/items/{item_id}/adjust", response=OpsActionResultOut, auth=_auth)
def ops_inventory_adjust(request, item_id: int, payload: OpsInventoryAdjustIn):
    _ensure_shop_context_if_strict(request)
    user = _require_ops_user(request)
    item = InventoryItem.objects.select_related("variant__product", "warehouse").filter(id=item_id).first()
    if item is None:
        raise HttpError(404, "Inventory item not found")

    next_qty = int(item.qty_on_hand) + int(payload.delta)
    if next_qty < 0:
        raise HttpError(400, "Adjustment would make qty_on_hand negative")
    if next_qty < int(item.qty_reserved):
        raise HttpError(400, "Adjustment would make qty_on_hand lower than qty_reserved")

    item.qty_on_hand = next_qty
    item.save(update_fields=["qty_on_hand", "updated_at"])
    return OpsActionResultOut(ok=True, message="Inventory adjusted", updated_ids=[item.id], failed_ids=[])
