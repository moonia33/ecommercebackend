from __future__ import annotations

from .shared import *


@router.get("/cart", response=CartOut)
def get_cart(request, country_code: str = "LT", channel: str = "normal"):
    _ensure_shop_context_if_strict(request)
    country_code = (country_code or "").strip().upper()
    if len(country_code) != 2:
        raise HttpError(400, "Invalid country_code")

    channel = (channel or "normal").strip().lower()
    if channel not in {"normal", "outlet"}:
        raise HttpError(400, "Invalid channel")

    cart = _get_cart_for_request(request, create=False)
    if cart is None:
        return CartOut(
            country_code=country_code,
            items=[],
            items_total=MoneyOut(
                currency="EUR",
                net=Decimal("0.00"),
                vat_rate=Decimal("0"),
                vat=Decimal("0.00"),
                gross=Decimal("0.00"),
            ),
        )
    items = list(
        CartItem.objects.select_related(
            "variant", "variant__product", "variant__product__tax_class", "offer")
        .filter(cart=cart)
        .order_by("id")
    )

    if items:
        try:
            track_event(
                request=request,
                name="view_cart",
                object_type="cart",
                object_id=int(cart.id),
                payload={"items_count": len(items)},
                country_code=country_code,
                channel=channel,
                language_code="",
                shop_id=_request_shop_id(request),
            )
        except Exception:
            pass

    out_items, items_total, delivery_window = _serialize_cart_items(
        items=items,
        country_code=country_code,
        channel=channel,
        shop_id=_request_shop_id(request),
        language_code=get_request_language_code(request),
    )
    return CartOut(
        country_code=country_code,
        items=out_items,
        items_total=items_total,
        delivery_window=delivery_window,
    )


@router.post("/cart/items", response=CartOut)
def add_cart_item(request, payload: CartItemAddIn, country_code: str = "LT"):
    _ensure_shop_context_if_strict(request)
    qty = int(payload.qty or 0)
    if qty <= 0:
        raise HttpError(400, "qty must be positive")

    shop_id = _request_shop_id(request)
    allowed_products = apply_shop_product_scope(
        qs=Product.objects.filter(is_active=True),
        shop_id=shop_id,
    )
    variant = (
        Variant.objects.select_related("product", "product__tax_class")
        .filter(id=payload.variant_id, is_active=True, product__in=allowed_products)
        .first()
    )
    if not variant:
        raise HttpError(404, "Variant not found")

    offer = None
    if getattr(payload, "offer_id", None):
        offer = (
            InventoryItem.objects.filter(
                id=int(payload.offer_id),
                variant_id=variant.id,
            )
            .select_related("warehouse")
            .first()
        )
        if not offer:
            raise HttpError(404, "Offer not found")
    else:
        # Default behaviour: allocate qty across offers in priority order.
        # This ensures e.g. returned stock (with special pricing) is sold first,
        # but any remainder falls back to other warehouses/offers.
        candidates = list(
            InventoryItem.objects.filter(
                variant_id=variant.id,
                offer_visibility=InventoryItem.OfferVisibility.NORMAL,
                qty_on_hand__gt=models.F("qty_reserved"),
            ).order_by("id")
        )
        candidates.sort(
            key=lambda ii: (
                -int(ii.offer_priority or 0),
                _effective_offer_unit_net(list_unit_net=Decimal(variant.price_eur), offer=ii),
                int(ii.id),
            )
        )

    cart = _get_cart_for_request(request, create=True)
    if cart is None:
        raise HttpError(400, "Session is not available")

    with transaction.atomic():
        # Variant-level cap: do not allow cart to exceed available inventory (all warehouses/offers).
        existing_variant_qty = (
            CartItem.objects.filter(cart=cart, variant_id=variant.id)
            .aggregate(total=models.Sum("qty"))
            .get("total")
            or 0
        )
        variant_available = inventory_available_for_variant(variant_id=variant.id)
        if int(existing_variant_qty) + int(qty) > int(variant_available):
            raise HttpError(409, "Not enough stock")

        # Explicit offer_id: keep existing semantics (single offer line).
        if offer is not None:
            inv = InventoryItem.objects.select_for_update().filter(id=int(offer.id)).first()
            if not inv:
                raise HttpError(404, "Offer not found")

            item = CartItem.objects.filter(cart=cart, offer=offer).first()
            desired_qty = int(qty) + (int(item.qty) if item else 0)
            offer_available = max(0, int(inv.qty_on_hand) - int(inv.qty_reserved))
            if desired_qty > offer_available:
                raise HttpError(409, "Not enough stock")

            if item:
                item.qty = desired_qty
                item.save(update_fields=["qty", "updated_at"])
            else:
                CartItem.objects.create(cart=cart, variant=variant, offer=offer, qty=int(qty))
        else:
            remaining = int(qty)

            # Allocate across offer candidates (available only).
            offer_ids = [int(c.id) for c in candidates]
            existing_offer_qty = {
                int(r["offer_id"]): int(r["total"] or 0)
                for r in CartItem.objects.filter(cart=cart, offer_id__in=offer_ids)
                .values("offer_id")
                .annotate(total=models.Sum("qty"))
            }

            # Lock candidate inventory rows while allocating.
            inv_locked = {
                int(i.id): i
                for i in InventoryItem.objects.select_for_update().filter(id__in=offer_ids)
            }

            for cand in candidates:
                if remaining <= 0:
                    break
                inv = inv_locked.get(int(cand.id))
                if not inv:
                    continue
                available = max(0, int(inv.qty_on_hand) - int(inv.qty_reserved))
                available -= int(existing_offer_qty.get(int(cand.id), 0))
                if available <= 0:
                    continue

                add_qty = min(available, remaining)
                existing = CartItem.objects.filter(cart=cart, offer=cand).first()
                if existing:
                    existing.qty = int(existing.qty) + int(add_qty)
                    existing.save(update_fields=["qty", "updated_at"])
                else:
                    CartItem.objects.create(cart=cart, variant=variant, offer=cand, qty=int(add_qty))
                remaining -= int(add_qty)

            if remaining > 0:
                raise HttpError(409, "Not enough stock")

    try:
        track_event(
            request=request,
            name="add_to_cart",
            object_type="variant",
            object_id=int(variant.id),
            payload={
                "qty": int(qty),
                "product_id": int(getattr(getattr(variant, "product", None), "id", 0) or 0),
                "product_slug": str(getattr(getattr(variant, "product", None), "slug", "") or ""),
                "sku": str(getattr(variant, "sku", "") or ""),
            },
            country_code=country_code,
            channel="normal",
            shop_id=_request_shop_id(request),
        )
    except Exception:
        pass

    return get_cart(request, country_code=country_code)


@router.patch("/cart/items/{item_id}", response=CartOut)
def update_cart_item(request, item_id: int, payload: CartItemUpdateIn, country_code: str = "LT"):
    _ensure_shop_context_if_strict(request)
    qty = int(payload.qty or 0)

    cart = _get_cart_for_request(request, create=False)
    if cart is None:
        raise HttpError(404, "Cart item not found")
    item = (
        CartItem.objects.select_related(
            "variant", "variant__product", "variant__product__tax_class", "offer")
        .filter(cart=cart, id=item_id)
        .first()
    )
    if not item:
        raise HttpError(404, "Cart item not found")

    prev_qty = int(getattr(item, "qty", 0) or 0)

    with transaction.atomic():
        if qty <= 0:
            item.delete()
        else:
            # Variant-level cap (sum of all cart lines for this variant).
            other_qty = (
                CartItem.objects.filter(cart=cart, variant_id=item.variant_id)
                .exclude(id=item.id)
                .aggregate(total=models.Sum("qty"))
                .get("total")
                or 0
            )
            variant_available = inventory_available_for_variant(variant_id=item.variant_id)
            if int(other_qty) + int(qty) > int(variant_available):
                raise HttpError(409, "Not enough stock")

            # Offer-level cap (if offer is selected, quantity can't exceed that offer's availability).
            if getattr(item, "offer_id", None):
                inv = InventoryItem.objects.select_for_update().filter(id=int(item.offer_id)).first()
                if not inv:
                    raise HttpError(409, "Not enough stock")
                offer_available = max(0, int(inv.qty_on_hand) - int(inv.qty_reserved))
                if int(qty) > int(offer_available):
                    raise HttpError(409, "Not enough stock")

            item.qty = qty
            item.save(update_fields=["qty", "updated_at"])

    try:
        name = "add_to_cart" if qty > prev_qty else "remove_from_cart"
        delta = abs(int(qty) - int(prev_qty))
        if delta > 0:
            track_event(
                request=request,
                name=name,
                object_type="variant",
                object_id=int(item.variant_id),
                payload={
                    "qty": int(delta),
                    "product_id": int(getattr(getattr(item.variant, "product", None), "id", 0) or 0),
                    "product_slug": str(getattr(getattr(item.variant, "product", None), "slug", "") or ""),
                    "sku": str(getattr(item.variant, "sku", "") or ""),
                },
                country_code=country_code,
                channel="normal",
                shop_id=_request_shop_id(request),
            )
    except Exception:
        pass

    return get_cart(request, country_code=country_code)


@router.delete("/cart/items/{item_id}", response=CartOut)
def delete_cart_item(request, item_id: int, country_code: str = "LT"):
    _ensure_shop_context_if_strict(request)
    cart = _get_cart_for_request(request, create=False)
    if cart is None:
        raise HttpError(404, "Cart item not found")

    item = (
        CartItem.objects.select_related("variant", "variant__product")
        .filter(cart=cart, id=item_id)
        .first()
    )
    if not item:
        raise HttpError(404, "Cart item not found")

    prev_qty = int(getattr(item, "qty", 0) or 0)
    deleted = item.delete()[0]
    if not deleted:
        raise HttpError(404, "Cart item not found")

    try:
        track_event(
            request=request,
            name="remove_from_cart",
            object_type="variant",
            object_id=int(item.variant_id),
            payload={
                "qty": int(prev_qty),
                "product_id": int(getattr(getattr(item.variant, "product", None), "id", 0) or 0),
                "product_slug": str(getattr(getattr(item.variant, "product", None), "slug", "") or ""),
                "sku": str(getattr(item.variant, "sku", "") or ""),
            },
            country_code=country_code,
            channel="normal",
            shop_id=_request_shop_id(request),
        )
    except Exception:
        pass

    return get_cart(request, country_code=country_code)
