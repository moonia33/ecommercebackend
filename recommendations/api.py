from __future__ import annotations

from ninja import Router
from ninja.errors import HttpError

from analytics.models import RecentlyViewedProduct
from analytics.services import _get_visitor_id_from_request
from api.i18n import get_request_language_code
from api.shop_context import get_request_shop, shop_context_enabled, shop_context_strict
from catalog.api.shared import _validate_country_and_channel
from catalog.api_schemas import ProductListOut
from catalog.models import Product
from catalog.services import serialize_products_listing
from catalog.shop_scope import apply_shop_product_scope

from .models import ProductRelation
from .services import (
    ordered_product_qs,
    popular_product_ids,
    recommend_product_ids,
)

router = Router(tags=["recommendations"])

VALID_KINDS = {k.value for k in ProductRelation.Kind}


def _shop_or_404_when_strict(request):
    shop = get_request_shop(request)
    if shop_context_enabled() and shop_context_strict() and shop is None:
        raise HttpError(404, "Shop not resolved for host")
    return shop


def _validate_kind(kind: str) -> str:
    kind = (kind or "cross_sell").strip().lower()
    if kind not in VALID_KINDS:
        raise HttpError(400, "Invalid kind")
    return kind


def _serialize_ids(request, ids, *, shop_id, country_code, channel):
    qs = ordered_product_qs(ids=ids, shop_id=shop_id, channel=channel)
    return serialize_products_listing(
        qs=qs,
        country_code=country_code,
        channel=channel,
        language_code=get_request_language_code(request),
    )


def _parse_args(request, kind, country_code, channel, limit):
    country_code, channel = _validate_country_and_channel(
        country_code=country_code, channel=channel
    )
    kind = _validate_kind(kind)
    limit = max(1, min(int(limit), 24))
    shop = _shop_or_404_when_strict(request)
    return kind, country_code, channel, limit, (int(shop.id) if shop else None)


def _user_from_request(request):
    from checkout.api.shared import _user_from_cookie_if_present

    return _user_from_cookie_if_present(request)


@router.get("/products/{slug}", response=list[ProductListOut])
def product_recommendations(
    request,
    slug: str,
    kind: str = "cross_sell",
    country_code: str = "LT",
    channel: str = "normal",
    limit: int = 12,
):
    """PDP recommendations for a relation kind (cross_sell|upsell|accessory|similar)."""
    kind, country_code, channel, limit, shop_id = _parse_args(
        request, kind, country_code, channel, limit
    )

    source = (
        apply_shop_product_scope(
            qs=Product.objects.filter(is_active=True), shop_id=shop_id
        )
        .filter(slug=slug)
        .first()
    )
    if not source:
        raise HttpError(404, "Product not found")

    ids = recommend_product_ids(
        seed_products=[source],
        kinds=(kind,),
        shop_id=shop_id,
        channel=channel,
        limit=limit,
    )
    return _serialize_ids(
        request, ids, shop_id=shop_id,
        country_code=country_code, channel=channel,
    )


@router.get("/cart", response=list[ProductListOut])
def cart_recommendations(
    request,
    kind: str = "cross_sell",
    country_code: str = "LT",
    channel: str = "normal",
    limit: int = 12,
):
    """Cross-sell for the whole cart; products already in cart are excluded."""
    kind, country_code, channel, limit, shop_id = _parse_args(
        request, kind, country_code, channel, limit
    )

    from checkout.api.shared import _get_cart_for_request

    cart = _get_cart_for_request(request, create=False)
    if cart is None:
        ids = popular_product_ids(
            shop_id=shop_id, channel=channel, limit=limit)
        return _serialize_ids(
            request, ids, shop_id=shop_id,
            country_code=country_code, channel=channel,
        )

    product_ids = [
        pid
        for pid in cart.items.filter(variant__isnull=False)
        .values_list("variant__product_id", flat=True)
        if pid
    ]
    seeds = list(
        apply_shop_product_scope(
            qs=Product.objects.filter(is_active=True, id__in=product_ids),
            shop_id=shop_id,
        )
    )
    ids = recommend_product_ids(
        seed_products=seeds,
        kinds=(kind,),
        shop_id=shop_id,
        channel=channel,
        limit=limit,
        exclude_ids=product_ids,
    )
    return _serialize_ids(
        request, ids, shop_id=shop_id,
        country_code=country_code, channel=channel,
    )


@router.get("/for-you", response=list[ProductListOut])
def for_you_recommendations(
    request,
    kind: str = "cross_sell",
    country_code: str = "LT",
    channel: str = "normal",
    limit: int = 12,
):
    """Personalized picks from the user/visitor view+purchase history."""
    kind, country_code, channel, limit, shop_id = _parse_args(
        request, kind, country_code, channel, limit
    )

    user = _user_from_request(request)
    visitor_id = _get_visitor_id_from_request(request)

    purchased_ids: set[int] = set()
    if user:
        from checkout.models import Order, OrderLine

        purchased_ids = set(
            OrderLine.objects.filter(
                order__user=user,
                order__status=Order.Status.PAID,
                variant__isnull=False,
            ).values_list("variant__product_id", flat=True)
        )

    viewed_qs = RecentlyViewedProduct.objects.all()
    if user:
        viewed_qs = viewed_qs.filter(user=user)
    elif visitor_id:
        viewed_qs = viewed_qs.filter(user__isnull=True, visitor_id=visitor_id)
    else:
        viewed_qs = RecentlyViewedProduct.objects.none()

    viewed_ids = list(
        viewed_qs.order_by("-last_viewed_at")
        .values_list("product_id", flat=True)[:24]
    )

    seed_ids = list(dict.fromkeys(list(purchased_ids) + viewed_ids))
    seeds = list(
        apply_shop_product_scope(
            qs=Product.objects.filter(is_active=True, id__in=seed_ids),
            shop_id=shop_id,
        )
    )
    ids = recommend_product_ids(
        seed_products=seeds,
        kinds=(kind,),
        shop_id=shop_id,
        channel=channel,
        limit=limit,
        exclude_ids=purchased_ids,
    )
    return _serialize_ids(
        request, ids, shop_id=shop_id,
        country_code=country_code, channel=channel,
    )


@router.get("/post-purchase/{order_id}", response=list[ProductListOut])
def post_purchase_recommendations(
    request,
    order_id: int,
    kind: str = "accessory",
    country_code: str = "LT",
    channel: str = "normal",
    limit: int = 8,
):
    """Accessory/cross-sell picks for a just-placed order (owner only)."""
    kind, country_code, channel, limit, shop_id = _parse_args(
        request, kind, country_code, channel, limit
    )

    user = _user_from_request(request)
    if not user:
        raise HttpError(401, "Unauthorized")

    from checkout.models import Order

    order = (
        Order.objects.filter(id=order_id, user=user)
        .prefetch_related("lines__variant")
        .first()
    )
    if not order:
        raise HttpError(404, "Order not found")

    product_ids = [
        ol.variant.product_id
        for ol in order.lines.all()
        if ol.variant_id and ol.variant
    ]
    seeds = list(
        apply_shop_product_scope(
            qs=Product.objects.filter(is_active=True, id__in=product_ids),
            shop_id=shop_id,
        )
    )
    ids = recommend_product_ids(
        seed_products=seeds,
        kinds=(kind, ProductRelation.Kind.CROSS_SELL),
        shop_id=shop_id,
        channel=channel,
        limit=limit,
        exclude_ids=product_ids,
    )
    return _serialize_ids(
        request, ids, shop_id=shop_id,
        country_code=country_code, channel=channel,
    )
