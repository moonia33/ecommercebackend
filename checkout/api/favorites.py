from __future__ import annotations

from django.db import models, transaction
from django.db.models import Min, Sum
from ninja.errors import HttpError

from .shared import *
from api.i18n import get_request_language_code
from catalog.api.shared import (
    _validate_country_and_channel,
    _visibility_for_channel,
    list_price_exprs,
    offer_price_exprs,
)
from catalog.services.product_listing import serialize_products_listing
from checkout.models import Favorite
from checkout.schemas import (
    FavoriteAddIn,
    FavoriteItemOut,
    FavoritesOut,
    FavoriteVariantOut,
)


def _session_key(request) -> str:
    if hasattr(request, "session"):
        try:
            return request.session.session_key or ""
        except Exception:
            return ""
    return ""


def _ensure_session(request) -> str:
    """Guest favorites live per Django session — create it on write."""
    key = _session_key(request)
    if not key and hasattr(request, "session"):
        request.session["fav_init"] = True
        try:
            request.session.save()
        except Exception:
            pass
        key = _session_key(request)
    return key


def _favorites_qs(request):
    """Favorite QS for the current identity (user > session), merging
    guest rows into the user on first access — same pattern as Cart."""
    _ensure_shop_context_if_strict(request)
    user = _user_from_cookie_if_present(request)
    shop_id = _request_shop_id(request)
    shop_filter = {"shop_id": shop_id} if shop_id is not None else {"shop__isnull": True}

    if user:
        session_key = _session_key(request)
        if session_key:
            guest_rows = list(
                Favorite.objects.filter(user=None, session_key=session_key, **shop_filter)
            )
            if guest_rows:
                with transaction.atomic():
                    existing = set(
                        Favorite.objects.filter(user=user, **shop_filter).values_list(
                            "variant_id", flat=True
                        )
                    )
                    for f in guest_rows:
                        if f.variant_id not in existing:
                            f.user = user
                            f.session_key = ""
                            f.save(update_fields=["user", "session_key"])
                            existing.add(f.variant_id)
                        else:
                            f.delete()
        return Favorite.objects.filter(user=user, **shop_filter)

    session_key = _session_key(request)
    if not session_key:
        return Favorite.objects.none()
    return Favorite.objects.filter(user=None, session_key=session_key, **shop_filter)


def _serialize_favorites(request, *, country_code: str, channel: str) -> FavoritesOut:
    favs = list(
        _favorites_qs(request).select_related("variant__product")
    )
    variant_ids = [int(f.variant_id) for f in favs]
    product_ids = [int(f.variant.product_id) for f in favs if f.variant_id]

    language_code = get_request_language_code(request)

    visibility = _visibility_for_channel(channel)
    min_price_expr, max_price_expr = list_price_exprs()
    offer_price_expr, offer_filter = offer_price_exprs(visibility)

    shop_id = _request_shop_id(request)
    products_qs = (
        Product.objects.filter(id__in=product_ids, is_active=True)
        .select_related("brand", "category", "tax_class")
        .prefetch_related(
            "images", "translations", "brand__translations", "category__translations"
        )
        .annotate(_min_variant_price=min_price_expr)
        .annotate(_max_variant_price=max_price_expr)
        .annotate(_min_offer_price=Min(offer_price_expr, filter=offer_filter))
    )
    products_qs = apply_shop_product_scope(qs=products_qs, shop_id=shop_id)
    products = {
        int(p["id"]): p
        for p in serialize_products_listing(
            qs=products_qs,
            country_code=country_code,
            channel=channel,
            language_code=language_code,
        )
    }

    # Visi aktyvūs favoritų produktų variantai (select'ui) + likučiai.
    variants = list(
        Variant.objects.filter(product_id__in=product_ids, is_active=True)
        .prefetch_related("inventory_items")
    )
    stock_by_variant: dict[int, int] = {}
    for v in variants:
        q = 0
        for ii in v.inventory_items.all():
            if ii.offer_visibility == visibility:
                q += max(int(ii.qty_on_hand) - int(ii.qty_reserved), 0)
        stock_by_variant[int(v.id)] = q

    variants_by_product: dict[int, list[FavoriteVariantOut]] = {}
    for v in variants:
        variants_by_product.setdefault(int(v.product_id), []).append(
            FavoriteVariantOut(
                id=int(v.id),
                label=v.name or v.sku,
                sku=v.sku,
                in_stock=stock_by_variant.get(int(v.id), 0) > 0,
            )
        )

    items: list[FavoriteItemOut] = []
    for f in favs:
        pid = int(f.variant.product_id)
        product = products.get(pid)
        if not product:
            continue  # neaktyvi / out-of-scope — praleidžiame
        items.append(
            FavoriteItemOut(
                favorite_id=int(f.id),
                variant_id=int(f.variant_id),
                product=product,
                variants=variants_by_product.get(pid, []),
            )
        )

    return FavoritesOut(items=items, variant_ids=variant_ids, count=len(items))


@router.get("/favorites/ids", response=dict)
def favorites_ids(request):
    """Lengvas variant/product id sąrašas (header count + heart state)."""
    pairs = list(
        _favorites_qs(request).select_related("variant").values_list(
            "variant_id", "variant__product_id"
        )
    )
    return {
        "variant_ids": [int(v) for v, _ in pairs],
        "product_ids": sorted({int(p) for _, p in pairs if p}),
        "count": len(pairs),
    }


@router.get("/favorites", response=FavoritesOut)
def favorites_list(request, country_code: str = "LT", channel: str = "normal"):
    country_code, channel = _validate_country_and_channel(
        country_code=country_code, channel=channel
    )
    return _serialize_favorites(request, country_code=country_code, channel=channel)


@router.post("/favorites", response=FavoritesOut)
def favorites_add(request, payload: FavoriteAddIn, country_code: str = "LT", channel: str = "normal"):
    country_code, channel = _validate_country_and_channel(
        country_code=country_code, channel=channel
    )
    user = _user_from_cookie_if_present(request)
    shop_id = _request_shop_id(request)

    if payload.variant_id:
        try:
            variant = Variant.objects.get(id=int(payload.variant_id), is_active=True)
        except Variant.DoesNotExist:
            raise HttpError(404, "Variant not found")
    elif payload.product_id:
        # Kortelės heart'as — be variantų pasirinkimo imamas pirmas aktyvus.
        variant = (
            Variant.objects.filter(product_id=int(payload.product_id), is_active=True)
            .order_by("id")
            .first()
        )
        if variant is None:
            raise HttpError(404, "Product not found")
    else:
        raise HttpError(400, "variant_id arba product_id privalomas")

    if user:
        fav, _ = Favorite.objects.get_or_create(
            user=user, shop_id=shop_id, variant=variant,
            defaults={"session_key": ""},
        )
    else:
        session_key = _ensure_session(request)
        if not session_key:
            raise HttpError(400, "Session unavailable")
        fav, _ = Favorite.objects.get_or_create(
            user=None, session_key=session_key, shop_id=shop_id, variant=variant
        )
    return _serialize_favorites(request, country_code=country_code, channel=channel)


@router.delete("/favorites/{variant_id}", response=FavoritesOut)
def favorites_remove(request, variant_id: int, country_code: str = "LT", channel: str = "normal"):
    country_code, channel = _validate_country_and_channel(
        country_code=country_code, channel=channel
    )
    _favorites_qs(request).filter(variant_id=int(variant_id)).delete()
    return _serialize_favorites(request, country_code=country_code, channel=channel)


@router.delete("/favorites/product/{product_id}", response=FavoritesOut)
def favorites_remove_product(request, product_id: int, country_code: str = "LT", channel: str = "normal"):
    """Kortelės untoggle — pašalina visus produkto variantus iš mėgstamų."""
    country_code, channel = _validate_country_and_channel(
        country_code=country_code, channel=channel
    )
    _favorites_qs(request).filter(variant__product_id=int(product_id)).delete()
    return _serialize_favorites(request, country_code=country_code, channel=channel)
