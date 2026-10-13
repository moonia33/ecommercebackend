from __future__ import annotations

from django.db.models import Case, Count, DecimalField, ExpressionWrapper, F, IntegerField, Min, Q, Value, When
from ninja import Router
from ninja.errors import HttpError

from api.shop_context import get_request_shop, shop_context_enabled, shop_context_strict
from catalog.api_schemas import ProductListOut
from catalog.models import InventoryItem, Product
from catalog.services import serialize_products_listing
from catalog.shop_scope import apply_shop_product_scope

router = Router(tags=["recommendations"])


def _shop_or_404_when_strict(request):
    shop = get_request_shop(request)
    if shop_context_enabled() and shop_context_strict() and shop is None:
        raise HttpError(404, "Shop not resolved for host")
    return shop


@router.get("/products/{slug}", response=list[ProductListOut])
def product_recommendations(
    request,
    slug: str,
    country_code: str = "LT",
    channel: str = "normal",
    limit: int = 12,
):
    country_code = (country_code or "").strip().upper()
    if len(country_code) != 2:
        raise HttpError(400, "Invalid country_code")

    channel = (channel or "normal").strip().lower()
    if channel not in {"normal", "outlet"}:
        raise HttpError(400, "Invalid channel")

    limit = max(1, min(int(limit), 24))

    visibility = (
        InventoryItem.OfferVisibility.OUTLET
        if channel == "outlet"
        else InventoryItem.OfferVisibility.NORMAL
    )

    min_price_expr = Min("variants__price_eur",
                         filter=Q(variants__is_active=True))

    offer_price_expr = Case(
        When(
            variants__inventory_items__never_discount=True,
            then=F("variants__price_eur"),
        ),
        When(
            variants__inventory_items__offer_price_override_eur__isnull=False,
            then=F("variants__inventory_items__offer_price_override_eur"),
        ),
        When(
            variants__inventory_items__offer_discount_percent__isnull=False,
            then=ExpressionWrapper(
                F("variants__price_eur")
                * (Value(100) - F("variants__inventory_items__offer_discount_percent"))
                / Value(100),
                output_field=DecimalField(max_digits=12, decimal_places=2),
            ),
        ),
        default=F("variants__price_eur"),
        output_field=DecimalField(max_digits=12, decimal_places=2),
    )

    offer_filter = (
        Q(variants__is_active=True)
        & Q(variants__inventory_items__offer_visibility=visibility)
        & Q(variants__inventory_items__qty_on_hand__gt=F("variants__inventory_items__qty_reserved"))
    )
    min_offer_price_expr = Min(offer_price_expr, filter=offer_filter)

    shop = _shop_or_404_when_strict(request)
    shop_id = int(shop.id) if shop else None

    source = apply_shop_product_scope(
        qs=Product.objects.filter(is_active=True),
        shop_id=shop_id,
    ).filter(slug=slug).first()
    if not source:
        raise HttpError(404, "Product not found")

    qs = (
        Product.objects.filter(is_active=True)
        .exclude(id=source.id)
        .select_related("brand", "category", "tax_class")
        .prefetch_related("images")
        .annotate(_min_variant_price=min_price_expr)
        .annotate(_min_offer_price=min_offer_price_expr)
    )
    qs = apply_shop_product_scope(qs=qs, shop_id=shop_id)

    qs = qs.annotate(
        _has_stock=Case(
            When(_min_offer_price__isnull=False, then=Value(1)),
            default=Value(0),
            output_field=IntegerField(),
        ),
        _same_category=Case(
            When(category_id=source.category_id, then=Value(1)),
            default=Value(0),
            output_field=IntegerField(),
        ),
        _same_brand=Case(
            When(brand_id=source.brand_id, then=Value(1)),
            default=Value(0),
            output_field=IntegerField(),
        ),
    )

    if channel == "outlet":
        qs = qs.filter(_min_offer_price__isnull=False)

    qs = qs.order_by("-_has_stock", "-_same_category",
                     "-_same_brand", "name", "id")[:limit]
    return serialize_products_listing(qs=qs, country_code=country_code, channel=channel)
