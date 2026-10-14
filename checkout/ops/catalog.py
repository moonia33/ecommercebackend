from __future__ import annotations

from .shared import *


@router.get("/catalog/products", response=OpsCatalogProductsOut, auth=_auth)
def ops_catalog_products(
    request,
    q: str = "",
    is_active: str = "",
    page: int = 1,
    page_size: int = 50,
):
    _ensure_shop_context_if_strict(request)
    _require_ops_user(request)
    page = max(1, int(page or 1))
    page_size = max(1, min(int(page_size or 50), 100))
    offset = (page - 1) * page_size

    qs = Product.objects.select_related("brand", "category").annotate(variants_count=Count("variants", distinct=True))
    if q:
        qs = qs.filter(Q(name__icontains=q) | Q(sku__icontains=q) | Q(slug__icontains=q))
    if is_active.strip().lower() in {"true", "false"}:
        qs = qs.filter(is_active=is_active.strip().lower() == "true")

    total_count = qs.count()
    rows = list(qs.order_by("id")[offset : offset + page_size])
    out = [
        OpsCatalogProductOut(
            id=int(p.id),
            sku=(p.sku or ""),
            name=(p.name or ""),
            slug=(p.slug or ""),
            is_active=bool(p.is_active),
            brand_name=(getattr(p.brand, "name", "") or "") if p.brand_id else "",
            category_name=(getattr(p.category, "name", "") or "") if p.category_id else "",
            variants_count=int(getattr(p, "variants_count", 0)),
        )
        for p in rows
    ]
    return OpsCatalogProductsOut(page=page, page_size=page_size, total_count=total_count, results=out)


@router.post("/catalog/products/{product_id}/toggle-active", response=OpsActionResultOut, auth=_auth)
def ops_catalog_toggle_product(request, product_id: int):
    _ensure_shop_context_if_strict(request)
    _require_ops_user(request)
    product = Product.objects.filter(id=product_id).first()
    if product is None:
        raise HttpError(404, "Product not found")
    product.is_active = not bool(product.is_active)
    product.save(update_fields=["is_active", "updated_at"])
    return OpsActionResultOut(ok=True, message="Product activity toggled", updated_ids=[product.id], failed_ids=[])


@router.post("/catalog/variants/{variant_id}/update-price", response=OpsActionResultOut, auth=_auth)
def ops_catalog_update_variant_price(request, variant_id: int, payload: OpsVariantPriceUpdateIn):
    _ensure_shop_context_if_strict(request)
    _require_ops_user(request)
    variant = Variant.objects.filter(id=variant_id).first()
    if variant is None:
        raise HttpError(404, "Variant not found")
    if payload.price_eur < 0:
        raise HttpError(400, "price_eur must be non-negative")
    variant.price_eur = payload.price_eur
    variant.save(update_fields=["price_eur", "updated_at"])
    return OpsActionResultOut(ok=True, message="Variant price updated", updated_ids=[variant.id], failed_ids=[])


@router.post("/catalog/offers/{inventory_item_id}/set-visibility", response=OpsActionResultOut, auth=_auth)
def ops_catalog_set_offer_visibility(request, inventory_item_id: int, payload: OpsOfferVisibilityIn):
    _ensure_shop_context_if_strict(request)
    _require_ops_user(request)
    inv = InventoryItem.objects.filter(id=inventory_item_id).first()
    if inv is None:
        raise HttpError(404, "Inventory offer not found")
    visibility = (payload.visibility or "").strip().upper()
    if visibility not in {InventoryItem.OfferVisibility.NORMAL, InventoryItem.OfferVisibility.OUTLET}:
        raise HttpError(400, "visibility must be NORMAL or OUTLET")
    inv.offer_visibility = visibility
    inv.save(update_fields=["offer_visibility", "updated_at"])
    return OpsActionResultOut(ok=True, message="Offer visibility updated", updated_ids=[inv.id], failed_ids=[])


@router.post("/catalog/bulk-actions", response=OpsActionResultOut, auth=_auth)
def ops_catalog_bulk_actions(request, payload: OpsCatalogBulkActionIn):
    _ensure_shop_context_if_strict(request)
    _require_ops_user(request)
    action = (payload.action or "").strip().lower()
    if action not in {"activate_products", "deactivate_products"}:
        raise HttpError(400, "Unsupported catalog bulk action")
    if not payload.product_ids:
        raise HttpError(400, "product_ids cannot be empty")

    value = action == "activate_products"
    updated = list(Product.objects.filter(id__in=payload.product_ids).values_list("id", flat=True))
    Product.objects.filter(id__in=updated).update(is_active=value, updated_at=timezone.now())
    return OpsActionResultOut(ok=True, message="Catalog bulk action applied", updated_ids=[int(x) for x in updated], failed_ids=[])
