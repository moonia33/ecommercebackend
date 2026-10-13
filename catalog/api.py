from __future__ import annotations

from decimal import Decimal

from django.conf import settings
from django.db.models import Case, Count, DecimalField, ExpressionWrapper, F, IntegerField, Min, Q, Value, When
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.contrib.auth import get_user_model
from django.utils import timezone
from ninja import Router
from ninja.errors import HttpError
from ninja.pagination import PageNumberPagination, paginate

from api.i18n import get_request_language_code
from api.shop_context import get_request_shop, shop_context_enabled, shop_context_strict
from pricing.services import get_vat_rate
from promotions.services import apply_promo_to_unit_net
from shipping.services import estimate_delivery_window

from analytics.services import track_event
from analytics.models import RecentlyViewedProduct

from .content_blocks import get_content_blocks_for_product
from .services import (
    apply_catalog_filters,
    apply_products_sorting,
    build_facets_payload,
    build_product_detail_payload,
    serialize_products_listing,
)
from .services.money import money_out as service_money_out
from .shop_pricing import resolve_shop_variant_price
from .shop_scope import apply_shop_product_scope
from .api_schemas import (
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
    VariantOptionOut,
    VariantOut,
)
from .models import (
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


class ProductPagination(PageNumberPagination):
    page_size = 20
    max_page_size = 100


@router.get("/recently-viewed", response=list[ProductListOut])
def recently_viewed(
    request,
    country_code: str = "LT",
    channel: str = "normal",
    limit: int | None = None,
):
    country_code = (country_code or "").strip().upper()
    if len(country_code) != 2:
        raise HttpError(400, "Invalid country_code")

    channel = (channel or "normal").strip().lower()
    if channel not in {"normal", "outlet"}:
        raise HttpError(400, "Invalid channel")

    try:
        default_limit = int(getattr(settings, "RECENTLY_VIEWED_MAX", 12))
    except Exception:
        default_limit = 12
    limit_v = int(limit) if limit is not None else default_limit
    limit_v = max(1, min(100, limit_v))

    user = None
    u = getattr(request, "user", None)
    if u is not None and getattr(u, "is_authenticated", False):
        user = u
    a = getattr(request, "auth", None)
    if user is None and isinstance(a, User):
        user = a
    if user is None and a is not None and getattr(a, "is_authenticated", False):
        user = a
    if user is None:
        try:
            from accounts.jwt_utils import decode_token

            cookie_name = getattr(
                settings, "AUTH_COOKIE_ACCESS_NAME", "access_token")
            token = (request.COOKIES.get(cookie_name) or "").strip()
            if token:
                payload = decode_token(token)
                if payload.get("type") == "access" and payload.get("sub"):
                    user = User.objects.get(
                        id=int(payload["sub"]), is_active=True)
        except Exception:
            user = None

    try:
        visitor_id = (request.COOKIES.get("vid") or "").strip()
    except Exception:
        visitor_id = ""

    qs = RecentlyViewedProduct.objects.all()
    if user is not None:
        qs = qs.filter(user=user)
    elif visitor_id:
        qs = qs.filter(user__isnull=True, visitor_id=visitor_id)
    else:
        return []

    ids = list(qs.order_by(
        "-last_viewed_at").values_list("product_id", flat=True)[:limit_v])
    if not ids:
        return []

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

    products_qs = (
        Product.objects.filter(is_active=True, id__in=ids)
        .select_related("brand", "category", "tax_class")
        .prefetch_related("images")
        .annotate(_min_variant_price=min_price_expr)
        .annotate(_min_offer_price=min_offer_price_expr)
    )
    products_qs = apply_shop_product_scope(
        qs=products_qs, shop_id=(int(shop.id) if shop else None))

    product_by_id = {int(p.id): p for p in products_qs}
    ordered = [product_by_id.get(int(pid)) for pid in ids]
    ordered = [p for p in ordered if p is not None]

    vat_cache: dict[int, Decimal] = {}

    def vat_rate_for(product: Product) -> Decimal:
        if not product.tax_class_id:
            raise HttpError(400, "Product has no tax_class assigned")
        key = int(product.tax_class_id)
        if key in vat_cache:
            return vat_cache[key]
        try:
            rate = get_vat_rate(country_code=country_code,
                                tax_class=product.tax_class)
        except LookupError:
            raise HttpError(
                400, "VAT rate not configured for country/tax_class")
        vat_cache[key] = Decimal(rate)
        return vat_cache[key]

    out: list[ProductListOut] = []
    for p in ordered:
        list_net = Decimal(p._min_variant_price if getattr(
            p, "_min_variant_price", None) is not None else 0)
        if getattr(p, "_min_offer_price", None) is not None:
            base_net = Decimal(p._min_offer_price)
        else:
            base_net = Decimal(list_net)
        rate = vat_rate_for(p)

        is_discounted_offer = bool(
            base_net and list_net and base_net < list_net)
        allow_additional_promotions = not is_discounted_offer

        sale_net, _rule = apply_promo_to_unit_net(
            base_unit_net=base_net,
            channel=channel,
            category_id=p.category_id,
            brand_id=p.brand_id,
            product_id=p.id,
            variant_id=None,
            customer_group_id=None,
            allow_additional_promotions=allow_additional_promotions,
            is_discounted_offer=is_discounted_offer,
        )

        compare_at_price = None
        discount_percent = _discount_percent(
            list_unit_net=list_net, sale_unit_net=sale_net)
        if discount_percent is not None:
            compare_at_price = _money_out(
                currency="EUR", unit_net=list_net, vat_rate=rate)

        imgs = list(p.images.all())
        imgs.sort(key=lambda i: (i.sort_order, i.id))
        images_out = []
        for img in imgs:
            if not img.url:
                continue
            list_avif = img.listing_avif_url or None
            list_webp = img.listing_webp_url or None
            list_url = list_avif or list_webp or img.url
            images_out.append(
                {
                    "avif_url": list_avif or (img.avif_url or None),
                    "webp_url": list_webp or (img.webp_url or None),
                    "url": list_url,
                    "alt_text": img.alt_text,
                    "sort_order": img.sort_order,
                }
            )
            if len(images_out) >= 2:
                break

        out.append(
            {
                "id": p.id,
                "sku": p.sku,
                "slug": p.slug,
                "name": p.name,
                "is_active": bool(p.is_active),
                "brand": {
                    "id": p.brand.id,
                    "slug": p.brand.slug,
                    "name": p.brand.name,
                }
                if p.brand
                else None,
                "category": {
                    "id": p.category.id,
                    "slug": p.category.slug,
                    "name": p.category.name,
                }
                if p.category
                else None,
                "images": images_out,
                "price": _money_out(currency="EUR", unit_net=Decimal(sale_net), vat_rate=rate),
                "compare_at_price": compare_at_price,
                "discount_percent": discount_percent,
            }
        )

    return out


@router.get("/categories", response=list[CategoryOut])
def categories(request):
    shop = _shop_or_404_when_strict(request)
    product_ids = set(
        apply_shop_product_scope(
            qs=Product.objects.filter(is_active=True),
            shop_id=(int(shop.id) if shop else None),
        ).values_list("id", flat=True)
    )
    qs = Category.objects.filter(is_active=True).order_by("name")
    return [
        {
            "id": c.id,
            "slug": c.slug,
            "name": c.name,
            "parent_id": c.parent_id,
            "description": c.description or "",
            "hero_image_url": (c.hero_url or None),
            "menu_icon_url": (c.menu_icon_url_resolved or None),
            "seo_title": getattr(c, "seo_title", "") or "",
            "seo_description": getattr(c, "seo_description", "") or "",
            "seo_keywords": getattr(c, "seo_keywords", "") or "",
        }
        for c in qs
        if (
            not product_ids
            or Product.objects.filter(is_active=True, category_id=c.id, id__in=product_ids).exists()
        )
    ]


@router.get("/categories/{slug}", response=CategoryDetailOut)
def category_detail(request, slug: str):
    c = Category.objects.filter(
        slug=slug, is_active=True).select_related("parent").first()
    if not c:
        raise HttpError(404, "Category not found")
    return {
        "id": c.id,
        "slug": c.slug,
        "name": c.name,
        "parent_id": c.parent_id,
        "description": c.description or "",
        "hero_image_url": (c.hero_url or None),
        "menu_icon_url": (c.menu_icon_url_resolved or None),
        "seo_title": getattr(c, "seo_title", "") or "",
        "seo_description": getattr(c, "seo_description", "") or "",
        "seo_keywords": getattr(c, "seo_keywords", "") or "",
    }


@router.get("/brands", response=list[BrandOut])
def brands(request):
    shop = _shop_or_404_when_strict(request)
    products_qs = apply_shop_product_scope(
        qs=Product.objects.filter(is_active=True),
        shop_id=(int(shop.id) if shop else None),
    )
    qs = Brand.objects.filter(
        is_active=True, products__in=products_qs).distinct().order_by("name")
    return [{"id": b.id, "slug": b.slug, "name": b.name} for b in qs]


@router.get("/brands/{slug}", response=BrandOut)
def brand_detail(request, slug: str):
    b = Brand.objects.filter(slug=slug, is_active=True).first()
    if not b:
        raise HttpError(404, "Brand not found")
    return {"id": b.id, "slug": b.slug, "name": b.name}


@router.get("/product-groups", response=list[ProductGroupOut])
def product_groups(request):
    qs = ProductGroup.objects.filter(is_active=True).order_by("name")
    return [
        {
            "id": g.id,
            "code": g.code,
            "name": g.name,
            "description": g.description or "",
        }
        for g in qs
    ]


@router.get("/product-groups/{code}", response=ProductGroupOut)
def product_group_detail(request, code: str):
    g = ProductGroup.objects.filter(code=code, is_active=True).first()
    if not g:
        raise HttpError(404, "Product group not found")
    return {"id": g.id, "code": g.code, "name": g.name, "description": g.description or ""}


@router.get("/features", response=list[FeatureOut])
def features(request):
    qs = (
        Feature.objects.filter(is_active=True, is_filterable=True)
        .prefetch_related("values")
        .order_by("sort_order", "code")
    )
    out: list[FeatureOut] = []
    for f in qs:
        vals = [v for v in f.values.all() if v.is_active]
        vals.sort(key=lambda v: (v.sort_order, v.value, v.id))
        out.append(
            {
                "id": f.id,
                "code": f.code,
                "name": f.name,
                "values": [{"id": v.id, "value": v.value} for v in vals],
            }
        )
    return out


@router.get("/option-types", response=list[OptionTypeOut])
def option_types(request):
    qs = (
        OptionType.objects.filter(is_active=True)
        .prefetch_related("values")
        .order_by("sort_order", "code")
    )
    out: list[OptionTypeOut] = []
    for t in qs:
        vals = [v for v in t.values.all() if v.is_active]
        vals.sort(key=lambda v: (v.sort_order, v.label, v.id))
        out.append(
            {
                "id": t.id,
                "code": t.code,
                "name": t.name,
                "display_type": t.display_type,
                "swatch_type": t.swatch_type,
                "values": [{"id": v.id, "code": v.code, "label": v.label} for v in vals],
            }
        )
    return out


@router.get("/products", response=list[ProductListOut])
@paginate(ProductPagination)
def products(
    request,
    country_code: str = "LT",
    channel: str = "normal",
    q: str | None = None,
    category_slug: str | None = None,
    brand_slug: str | None = None,
    group_code: str | None = None,
    feature: str | None = None,
    option: str | None = None,
    sort: str | None = None,
    in_stock_only: bool = False,
):
    country_code = (country_code or "").strip().upper()
    if len(country_code) != 2:
        raise HttpError(400, "Invalid country_code")

    channel = (channel or "normal").strip().lower()
    if channel not in {"normal", "outlet"}:
        raise HttpError(400, "Invalid channel")

    visibility = (
        InventoryItem.OfferVisibility.OUTLET
        if channel == "outlet"
        else InventoryItem.OfferVisibility.NORMAL
    )

    # Representative list price: min active variant price (net) per product.
    min_price_expr = Min("variants__price_eur",
                         filter=Q(variants__is_active=True))

    # Representative offer price: min effective offer sale price (net) per product.
    # Only consider inventory items with available stock and matching visibility.
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

    qs = (
        Product.objects.filter(is_active=True)
        .select_related("brand", "category", "tax_class")
        .prefetch_related("images")
        .annotate(_min_variant_price=min_price_expr)
        .annotate(_min_offer_price=min_offer_price_expr)
    )
    qs = apply_shop_product_scope(
        qs=qs, shop_id=(int(shop.id) if shop else None))

    qs = qs.annotate(
        _has_stock=Case(
            When(_min_offer_price__isnull=False, then=Value(1)),
            default=Value(0),
            output_field=IntegerField(),
        )
    )

    if in_stock_only:
        qs = qs.filter(_has_stock=1)

    qs = apply_products_sorting(qs=qs, sort=sort)

    qs, _selected_category = apply_catalog_filters(
        qs=qs,
        q=q,
        category_slug=category_slug,
        brand_slug=brand_slug,
        group_code=group_code,
        feature=feature,
        option=option,
    )

    if channel == "outlet":
        qs = qs.filter(_min_offer_price__isnull=False)

    return serialize_products_listing(qs=qs, country_code=country_code, channel=channel)


@router.get("/products/facets", response=CatalogFacetsOut)
def product_facets(
    request,
    country_code: str = "LT",
    channel: str = "normal",
    q: str | None = None,
    category_slug: str | None = None,
    brand_slug: str | None = None,
    group_code: str | None = None,
    feature: str | None = None,
    option: str | None = None,
):
    country_code = (country_code or "").strip().upper()
    if len(country_code) != 2:
        raise HttpError(400, "Invalid country_code")

    channel = (channel or "normal").strip().lower()
    if channel not in {"normal", "outlet"}:
        raise HttpError(400, "Invalid channel")

    visibility = (
        InventoryItem.OfferVisibility.OUTLET
        if channel == "outlet"
        else InventoryItem.OfferVisibility.NORMAL
    )

    offer_filter = (
        Q(variants__is_active=True)
        & Q(variants__inventory_items__offer_visibility=visibility)
        & Q(variants__inventory_items__qty_on_hand__gt=F("variants__inventory_items__qty_reserved"))
    )
    shop = _shop_or_404_when_strict(request)
    qs = Product.objects.filter(is_active=True).annotate(
        _has_offer=Count("id", filter=offer_filter)
    )
    qs = apply_shop_product_scope(
        qs=qs, shop_id=(int(shop.id) if shop else None))
    if channel == "outlet":
        qs = qs.filter(_has_offer__gt=0)

    qs, selected_category = apply_catalog_filters(
        qs=qs,
        q=q,
        category_slug=category_slug,
        brand_slug=brand_slug,
        group_code=group_code,
        feature=feature,
        option=option,
    )

    product_ids = list(qs.values_list("id", flat=True))
    return build_facets_payload(product_ids=product_ids, selected_category=selected_category)


@router.get("/categories/{slug}/products", response=list[ProductListOut])
@paginate(ProductPagination)
def category_products(
    request,
    slug: str,
    country_code: str = "LT",
    channel: str = "normal",
    q: str | None = None,
    brand_slug: str | None = None,
    group_code: str | None = None,
    feature: str | None = None,
    option: str | None = None,
    sort: str | None = None,
    in_stock_only: bool = False,
):
    return products(
        request,
        country_code=country_code,
        channel=channel,
        q=q,
        category_slug=slug,
        brand_slug=brand_slug,
        group_code=group_code,
        feature=feature,
        option=option,
        sort=sort,
        in_stock_only=in_stock_only,
    )


@router.get("/brands/{slug}/products", response=list[ProductListOut])
@paginate(ProductPagination)
def brand_products(
    request,
    slug: str,
    country_code: str = "LT",
    channel: str = "normal",
    q: str | None = None,
    category_slug: str | None = None,
    group_code: str | None = None,
    feature: str | None = None,
    option: str | None = None,
    sort: str | None = None,
    in_stock_only: bool = False,
):
    return products(
        request,
        country_code=country_code,
        channel=channel,
        q=q,
        category_slug=category_slug,
        brand_slug=slug,
        group_code=group_code,
        feature=feature,
        option=option,
        sort=sort,
        in_stock_only=in_stock_only,
    )


@router.get("/product-groups/{code}/products", response=list[ProductListOut])
@paginate(ProductPagination)
def product_group_products(
    request,
    code: str,
    country_code: str = "LT",
    channel: str = "normal",
    q: str | None = None,
    category_slug: str | None = None,
    brand_slug: str | None = None,
    feature: str | None = None,
    option: str | None = None,
    sort: str | None = None,
    in_stock_only: bool = False,
):
    return products(
        request,
        country_code=country_code,
        channel=channel,
        q=q,
        category_slug=category_slug,
        brand_slug=brand_slug,
        group_code=code,
        feature=feature,
        option=option,
        sort=sort,
        in_stock_only=in_stock_only,
    )


@router.post("/back-in-stock/subscribe", response=BackInStockSubscribeOut)
def back_in_stock_subscribe(request, payload: BackInStockSubscribeIn):
    email = (payload.email or "").strip().lower()
    try:
        validate_email(email)
    except ValidationError:
        raise HttpError(400, "Invalid email")

    channel = (getattr(payload, "channel", None) or "normal").strip().lower()
    if channel not in {"normal", "outlet"}:
        raise HttpError(400, "Invalid channel")

    product = None
    variant = None

    if payload.variant_id:
        variant = (
            Variant.objects.filter(id=int(payload.variant_id))
            .select_related("product")
            .first()
        )
        if not variant:
            raise HttpError(404, "Variant not found")
        product = variant.product

    if payload.product_id:
        product = Product.objects.filter(id=int(payload.product_id)).first()
        if not product:
            raise HttpError(404, "Product not found")

    if not product and not variant:
        raise HttpError(400, "product_id or variant_id is required")

    language_code = get_request_language_code(request)

    obj, created = BackInStockSubscription.objects.get_or_create(
        email=email,
        product=product,
        variant=variant,
        channel=channel,
        defaults={"is_active": True, "language_code": language_code},
    )
    if not created and not obj.is_active:
        obj.is_active = True
        obj.save(update_fields=["is_active"])

    if not created and (obj.language_code or "").strip().lower() != (language_code or "").strip().lower():
        obj.language_code = language_code
        obj.save(update_fields=["language_code"])

    return {"status": "ok"}


@router.get("/products/{slug}", response=ProductDetailOut)
def product_detail(
    request,
    slug: str,
    country_code: str = "LT",
    channel: str = "normal",
    language_code: str | None = None,
):
    country_code = (country_code or "").strip().upper()
    if len(country_code) != 2:
        raise HttpError(400, "Invalid country_code")

    channel = (channel or "normal").strip().lower()
    if channel not in {"normal", "outlet"}:
        raise HttpError(400, "Invalid channel")

    shop = _shop_or_404_when_strict(request)
    products_qs = apply_shop_product_scope(
        qs=Product.objects.filter(is_active=True),
        shop_id=(int(shop.id) if shop else None),
    )
    product = (
        products_qs.filter(slug=slug)
        .select_related("brand", "category", "tax_class")
        .prefetch_related(
            "images",
            "feature_values__feature",
            "feature_values__feature_value",
            "variants",
            "variants__option_values__option_type",
            "variants__option_values__option_value",
            "variants__inventory_items",
        )
        .first()
    )
    if not product:
        raise HttpError(404, "Product not found")

    try:
        track_event(
            request=request,
            name="product_view",
            object_type="product",
            object_id=int(product.id),
            payload={"slug": product.slug, "sku": product.sku},
            country_code=country_code,
            channel=channel,
            language_code=(language_code or ""),
            shop_id=(int(shop.id) if shop else None),
        )
    except Exception:
        pass

    return build_product_detail_payload(
        product=product,
        shop_id=(int(shop.id) if shop else None),
        country_code=country_code,
        channel=channel,
        language_code=language_code,
    )
