from __future__ import annotations

from decimal import Decimal

from django.conf import settings
from django.db.models import (
    Case,
    DecimalField,
    ExpressionWrapper,
    F,
    Max,
    Min,
    Q,
    Value,
    When,
)
from django.contrib.auth import get_user_model
from ninja import Router
from ninja.errors import HttpError
from ninja.pagination import PageNumberPagination

from api.i18n import get_request_language_code
from api.shop_context import get_request_shop, shop_context_enabled, shop_context_strict
from pricing.services import get_vat_rate
from promotions.services import apply_promo_to_unit_net
from shipping.services import estimate_delivery_window

from analytics.services import track_event
from analytics.models import RecentlyViewedProduct

from ..localization import (
    localize_brand,
    localize_category,
    localize_feature,
    localize_feature_value,
    localize_option_type,
    localize_option_value,
    localize_product,
    resolve_brand_by_slug,
    resolve_category_by_slug,
    resolve_product_by_slug,
)
from ..content_blocks import get_content_blocks_for_product
from ..services import (
    apply_catalog_filters,
    apply_products_sorting,
    build_facets_payload,
    build_product_detail_payload,
    serialize_products_listing,
)
from ..services.money import money_out as service_money_out
from ..shop_pricing import resolve_shop_variant_price
from ..shop_scope import apply_shop_product_scope
from ..api_schemas import (
    BackInStockSubscribeIn,
    BackInStockSubscribeOut,
    BrandOut,
    BrandRefOut,
    CatalogFacetsOut,
    CategoryOut,
    CategoryDetailOut,
    CategoryRefOut,
    FeatureOut,
    OptionTypeOut,
    MoneyOut,
    ProductDetailOut,
    ProductGroupOut,
    ProductImageOut,
    ProductListOut,
    ProductVideoOut,
    VariantOptionOut,
    VariantOut,
)
from ..models import (
    BackInStockSubscription,
    Brand,
    Category,
    Feature,
    InventoryItem,
    OptionType,
    Product,
    ProductGroup,
    ProductOptionType,
    Variant,
)

router = Router(tags=["catalog"])


User = get_user_model()


def _shop_or_404_when_strict(request):
    shop = get_request_shop(request)
    if shop_context_enabled() and shop_context_strict() and shop is None:
        raise HttpError(404, "Shop not resolved for host")
    return shop


def _money_out(*, currency: str, unit_net: Decimal, vat_rate: Decimal) -> MoneyOut:
    return service_money_out(currency=currency, unit_net=unit_net, vat_rate=vat_rate)


def _effective_offer_unit_net(*, list_unit_net: Decimal, offer: InventoryItem) -> Decimal:
    if bool(getattr(offer, "never_discount", False)):
        return Decimal(list_unit_net)
    if offer.offer_price_override_eur is not None:
        return Decimal(offer.offer_price_override_eur)
    if offer.offer_discount_percent is not None:
        pct = int(offer.offer_discount_percent)
        pct = max(0, min(100, pct))
        return (Decimal(list_unit_net) * (Decimal(100 - pct) / Decimal(100))).quantize(Decimal("0.01"))
    return Decimal(list_unit_net)


def _discount_percent(*, list_unit_net: Decimal, sale_unit_net: Decimal) -> int | None:
    list_unit_net = Decimal(list_unit_net)
    sale_unit_net = Decimal(sale_unit_net)
    if list_unit_net <= 0:
        return None
    if sale_unit_net >= list_unit_net:
        return None
    pct = int(((list_unit_net - sale_unit_net) / list_unit_net *
              Decimal(100)).quantize(Decimal("1")))
    return max(0, min(100, pct))


def _visibility_for_channel(channel: str) -> str:
    return (
        InventoryItem.OfferVisibility.OUTLET
        if channel == "outlet"
        else InventoryItem.OfferVisibility.NORMAL
    )


def _validate_country_and_channel(*, country_code: str, channel: str) -> tuple[str, str]:
    country_code = (country_code or "").strip().upper()
    if len(country_code) != 2:
        raise HttpError(400, "Invalid country_code")
    channel = (channel or "normal").strip().lower()
    if channel not in {"normal", "outlet"}:
        raise HttpError(400, "Invalid channel")
    return country_code, channel


def list_price_exprs():
    """Representative min/max active variant list prices (net) per product."""
    min_price_expr = Min("variants__price_eur", filter=Q(variants__is_active=True))
    max_price_expr = Max("variants__price_eur", filter=Q(variants__is_active=True))
    return min_price_expr, max_price_expr


def offer_price_exprs(visibility: str):
    """Effective offer price expression + availability filter for annotations."""
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
    return offer_price_expr, offer_filter


class ProductPagination(PageNumberPagination):
    page_size = 20
    max_page_size = 100
