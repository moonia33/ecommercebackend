from __future__ import annotations

from decimal import Decimal

from django.conf import settings
from django.db.models import Min
from ninja.errors import HttpError

from accounts.jwt_utils import decode_token

from .shared import (
    ProductListOut,
    RecentlyViewedProduct,
    User,
    _discount_percent,
    _money_out,
    _shop_or_404_when_strict,
    _validate_country_and_channel,
    _visibility_for_channel,
    apply_promo_to_unit_net,
    apply_shop_product_scope,
    get_request_language_code,
    get_vat_rate,
    list_price_exprs,
    localize_brand,
    localize_category,
    localize_product,
    offer_price_exprs,
    router,
)
from ..models import Product
from ..services.image_out import image_out


@router.get("/recently-viewed", response=list[ProductListOut])
def recently_viewed(
    request,
    country_code: str = "LT",
    channel: str = "normal",
    limit: int | None = None,
):
    country_code, channel = _validate_country_and_channel(
        country_code=country_code, channel=channel
    )

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

    language_code = get_request_language_code(request)

    visibility = _visibility_for_channel(channel)
    min_price_expr, max_price_expr = list_price_exprs()
    offer_price_expr, offer_filter = offer_price_exprs(visibility)
    min_offer_price_expr = Min(offer_price_expr, filter=offer_filter)

    shop = _shop_or_404_when_strict(request)

    products_qs = (
        Product.objects.filter(is_active=True, id__in=ids)
        .select_related("brand", "category", "tax_class")
        .prefetch_related("images", "translations", "brand__translations", "category__translations")
        .annotate(_min_variant_price=min_price_expr)
        .annotate(_max_variant_price=max_price_expr)
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
        product_loc = localize_product(p, language_code=language_code)
        brand_loc = localize_brand(p.brand, language_code=language_code) if p.brand else None
        category_loc = localize_category(p.category, language_code=language_code) if p.category else None

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

        price_to = None
        max_variant_net = getattr(p, "_max_variant_price", None)
        if max_variant_net is not None and Decimal(max_variant_net) > list_net:
            price_to = _money_out(
                currency="EUR", unit_net=Decimal(max_variant_net), vat_rate=rate)

        imgs = list(p.images.all())
        imgs.sort(key=lambda i: (i.sort_order, i.id))
        images_out = []
        for img in imgs:
            o = image_out(img, context="listing")
            if not o:
                continue
            images_out.append(o)
            if len(images_out) >= 2:
                break

        out.append(
            {
                "id": p.id,
                "sku": p.sku,
                "slug": product_loc["slug"],
                "name": product_loc["name"],
                "is_active": bool(p.is_active),
                "product_group_code": p.product_group_code or "",
                "brand": {
                    "id": p.brand.id,
                    "slug": brand_loc["slug"],
                    "name": brand_loc["name"],
                }
                if p.brand and brand_loc
                else None,
                "category": {
                    "id": p.category.id,
                    "slug": category_loc["slug"],
                    "name": category_loc["name"],
                }
                if p.category and category_loc
                else None,
                "images": images_out,
                "price": _money_out(currency="EUR", unit_net=Decimal(sale_net), vat_rate=rate),
                "price_to": price_to,
                "compare_at_price": compare_at_price,
                "discount_percent": discount_percent,
            }
        )

    return out
