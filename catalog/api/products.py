from __future__ import annotations

from decimal import Decimal

from django.db.models import Case, Count, IntegerField, Min, Value, When
from ninja.errors import HttpError
from ninja.pagination import paginate

from .shared import (
    ProductPagination,
    _money_out,
    _shop_or_404_when_strict,
    _validate_country_and_channel,
    _visibility_for_channel,
    apply_catalog_filters,
    apply_products_sorting,
    apply_shop_product_scope,
    build_facets_payload,
    build_product_detail_payload,
    get_request_language_code,
    list_price_exprs,
    offer_price_exprs,
    resolve_product_by_slug,
    router,
    serialize_products_listing,
    track_event,
)
from ..models import Product
from ..api_schemas import CatalogFacetsOut, ProductDetailOut, ProductListOut


@router.get("/products", response=list[ProductListOut])
@paginate(ProductPagination)
def products(
    request,
    country_code: str = "LT",
    channel: str = "normal",
    language_code: str | None = None,
    q: str | None = None,
    category_slug: str | None = None,
    brand_slug: str | None = None,
    group_code: str | None = None,
    feature: str | None = None,
    option: str | None = None,
    sort: str | None = None,
    in_stock_only: bool = False,
):
    country_code, channel = _validate_country_and_channel(
        country_code=country_code, channel=channel
    )
    if language_code is None:
        language_code = get_request_language_code(request)

    visibility = _visibility_for_channel(channel)
    min_price_expr, max_price_expr = list_price_exprs()
    offer_price_expr, offer_filter = offer_price_exprs(visibility)
    min_offer_price_expr = Min(offer_price_expr, filter=offer_filter)

    shop = _shop_or_404_when_strict(request)

    qs = (
        Product.objects.filter(is_active=True)
        .select_related("brand", "category", "tax_class")
        .prefetch_related("images", "translations", "brand__translations", "category__translations")
        .annotate(_min_variant_price=min_price_expr)
        .annotate(_max_variant_price=max_price_expr)
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
        language_code=language_code,
        group_code=group_code,
        feature=feature,
        option=option,
    )

    if channel == "outlet":
        qs = qs.filter(_min_offer_price__isnull=False)

    return serialize_products_listing(
        qs=qs,
        country_code=country_code,
        channel=channel,
        language_code=language_code,
    )


@router.get("/products/facets", response=CatalogFacetsOut)
def product_facets(
    request,
    country_code: str = "LT",
    channel: str = "normal",
    language_code: str | None = None,
    q: str | None = None,
    category_slug: str | None = None,
    brand_slug: str | None = None,
    group_code: str | None = None,
    feature: str | None = None,
    option: str | None = None,
):
    country_code, channel = _validate_country_and_channel(
        country_code=country_code, channel=channel
    )
    if language_code is None:
        language_code = get_request_language_code(request)

    visibility = _visibility_for_channel(channel)
    _offer_price_expr, offer_filter = offer_price_exprs(visibility)

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
        language_code=language_code,
        group_code=group_code,
        feature=feature,
        option=option,
    )

    product_ids = list(qs.values_list("id", flat=True))
    return build_facets_payload(
        product_ids=product_ids,
        selected_category=selected_category,
        language_code=language_code,
    )


@router.get("/categories/{slug}/products", response=list[ProductListOut])
@paginate(ProductPagination)
def category_products(
    request,
    slug: str,
    country_code: str = "LT",
    channel: str = "normal",
    language_code: str | None = None,
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
        language_code=language_code,
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
    language_code: str | None = None,
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
        language_code=language_code,
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
    language_code: str | None = None,
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
        language_code=language_code,
        q=q,
        category_slug=category_slug,
        brand_slug=brand_slug,
        group_code=code,
        feature=feature,
        option=option,
        sort=sort,
        in_stock_only=in_stock_only,
    )


@router.get("/products/{slug}", response=ProductDetailOut)
def product_detail(
    request,
    slug: str,
    country_code: str = "LT",
    channel: str = "normal",
    language_code: str | None = None,
):
    country_code, channel = _validate_country_and_channel(
        country_code=country_code, channel=channel
    )
    if language_code is None:
        language_code = get_request_language_code(request)

    shop = _shop_or_404_when_strict(request)
    products_qs = apply_shop_product_scope(
        qs=Product.objects.filter(is_active=True),
        shop_id=(int(shop.id) if shop else None),
    )
    resolved = resolve_product_by_slug(slug=slug, language_code=language_code, is_active=True)
    if resolved is None:
        raise HttpError(404, "Product not found")
    product = (
        products_qs.filter(id=resolved.id)
        .select_related("brand", "category", "tax_class")
        .prefetch_related(
            "images",
            "videos",
            "translations",
            "brand__translations",
            "category__translations",
            "feature_values__feature__translations",
            "feature_values__feature_value__translations",
            "feature_values__feature",
            "feature_values__feature_value",
            "variants",
            "variants__option_values__option_type",
            "variants__option_values__option_type__translations",
            "variants__option_values__option_value",
            "variants__option_values__option_value__translations",
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
