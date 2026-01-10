from __future__ import annotations

from decimal import Decimal

from django.db.models import Case, Count, DecimalField, ExpressionWrapper, F, Min, Q, Value, When
from ninja import Router
from ninja.errors import HttpError
from ninja.pagination import PageNumberPagination, paginate

from pricing.services import compute_vat, get_vat_rate

from .api_schemas import (
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
    Brand,
    Category,
    Feature,
    InventoryItem,
    OptionType,
    Product,
    ProductFeatureValue,
    ProductGroup,
    ProductOptionType,
    Variant,
    VariantOptionValue,
)
from promotions.services import apply_promo_to_unit_net

router = Router(tags=["catalog"])


def _money_out(*, currency: str, unit_net: Decimal, vat_rate: Decimal) -> MoneyOut:
    b = compute_vat(unit_net=Decimal(unit_net),
                    vat_rate=Decimal(vat_rate), qty=1)
    return {
        "currency": currency,
        "net": b.unit_net,
        "vat_rate": b.vat_rate,
        "vat": b.unit_vat,
        "gross": b.unit_gross,
    }


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
    pct = int(((list_unit_net - sale_unit_net) / list_unit_net * Decimal(100)).quantize(Decimal("1")))
    return max(0, min(100, pct))


class ProductPagination(PageNumberPagination):
    page_size = 20
    max_page_size = 100


def _parse_pairs(value: str | None) -> list[tuple[str, str]]:
    if not value:
        return []
    out: list[tuple[str, str]] = []
    for part in [p.strip() for p in value.split(",") if p.strip()]:
        if ":" not in part:
            raise HttpError(400, "Invalid pair format; expected code:value")
        k, v = part.split(":", 1)
        k = k.strip()
        v = v.strip()
        if not k or not v:
            raise HttpError(400, "Invalid pair format; expected code:value")
        out.append((k, v))
    return out


def _descendant_category_ids(*, root_id: int) -> list[int]:
    rows = Category.objects.filter(is_active=True).values("id", "parent_id")
    children: dict[int, list[int]] = {}
    for r in rows:
        pid = r["parent_id"]
        if pid is None:
            continue
        children.setdefault(int(pid), []).append(int(r["id"]))

    out: list[int] = []
    stack = [int(root_id)]
    seen: set[int] = set()
    while stack:
        cid = stack.pop()
        if cid in seen:
            continue
        seen.add(cid)
        out.append(cid)
        stack.extend(children.get(cid, []))
    return out


@router.get("/categories", response=list[CategoryOut])
def categories(request):
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
    qs = Brand.objects.filter(is_active=True).order_by("name")
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
    min_price_expr = Min("variants__price_eur", filter=Q(variants__is_active=True))

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

    qs = (
        Product.objects.filter(is_active=True)
        .select_related("brand", "category", "tax_class")
        .prefetch_related("images")
        .annotate(_min_variant_price=min_price_expr)
        .annotate(_min_offer_price=min_offer_price_expr)
        .order_by("name", "id")
    )

    if q:
        qv = q.strip()
        if qv:
            qs = qs.filter(Q(name__icontains=qv) | Q(slug__icontains=qv) | Q(sku__icontains=qv))

    if category_slug:
        c = Category.objects.filter(slug=category_slug, is_active=True).first()
        if not c:
            raise HttpError(404, "Category not found")
        ids = _descendant_category_ids(root_id=int(c.id))
        qs = qs.filter(category_id__in=ids)

    if brand_slug:
        b = Brand.objects.filter(slug=brand_slug, is_active=True).first()
        if not b:
            raise HttpError(404, "Brand not found")
        qs = qs.filter(brand_id=b.id)

    if group_code:
        g = ProductGroup.objects.filter(code=group_code, is_active=True).first()
        if not g:
            raise HttpError(404, "Product group not found")
        qs = qs.filter(group_id=g.id)

    for f_code, f_val in _parse_pairs(feature):
        qs = qs.filter(
            feature_values__feature__code=f_code,
            feature_values__feature_value__value=f_val,
        )

    for o_type, o_val in _parse_pairs(option):
        qs = qs.filter(
            variants__option_values__option_type__code=o_type,
            variants__option_values__option_value__code=o_val,
        )

    if feature or option:
        qs = qs.distinct()

    if channel == "outlet":
        qs = qs.filter(_min_offer_price__isnull=False)

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
    for p in qs:
        if getattr(p, "_min_offer_price", None) is not None:
            base_net = Decimal(p._min_offer_price)
        else:
            base_net = Decimal(p._min_variant_price if p._min_variant_price is not None else 0)
        rate = vat_rate_for(p)

        sale_net, _rule = apply_promo_to_unit_net(
            base_unit_net=base_net,
            channel=channel,
            category_id=p.category_id,
            brand_id=p.brand_id,
            product_id=p.id,
            variant_id=None,
            customer_group_id=None,
            allow_additional_promotions=True,
            is_discounted_offer=False,
        )

        compare_at_price = None
        discount_percent = _discount_percent(list_unit_net=base_net, sale_unit_net=sale_net)
        if discount_percent is not None:
            compare_at_price = _money_out(currency="EUR", unit_net=base_net, vat_rate=rate)

        imgs = list(p.images.all())
        imgs.sort(key=lambda i: (i.sort_order, i.id))
        images_out = []
        for img in imgs:
            if not img.url:
                continue

            # For product grid/listing use square (1:1) renditions if available.
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
    qs = Product.objects.filter(is_active=True).annotate(
        _has_offer=Count("id", filter=offer_filter)
    )
    if channel == "outlet":
        qs = qs.filter(_has_offer__gt=0)

    if q:
        qv = q.strip()
        if qv:
            qs = qs.filter(Q(name__icontains=qv) | Q(slug__icontains=qv) | Q(sku__icontains=qv))

    selected_category: Category | None = None
    if category_slug:
        selected_category = Category.objects.filter(slug=category_slug, is_active=True).first()
        if not selected_category:
            raise HttpError(404, "Category not found")
        ids = _descendant_category_ids(root_id=int(selected_category.id))
        qs = qs.filter(category_id__in=ids)

    if brand_slug:
        b = Brand.objects.filter(slug=brand_slug, is_active=True).first()
        if not b:
            raise HttpError(404, "Brand not found")
        qs = qs.filter(brand_id=b.id)

    if group_code:
        g = ProductGroup.objects.filter(code=group_code, is_active=True).first()
        if not g:
            raise HttpError(404, "Product group not found")
        qs = qs.filter(group_id=g.id)

    for f_code, f_val in _parse_pairs(feature):
        qs = qs.filter(
            feature_values__feature__code=f_code,
            feature_values__feature_value__value=f_val,
        )

    for o_type, o_val in _parse_pairs(option):
        qs = qs.filter(
            variants__option_values__option_type__code=o_type,
            variants__option_values__option_value__code=o_val,
        )

    if feature or option:
        qs = qs.distinct()

    product_ids = list(qs.values_list("id", flat=True))
    if not product_ids:
        return {
            "categories": [],
            "brands": [],
            "product_groups": [],
            "features": [],
            "option_types": [],
        }

    if selected_category:
        cat_qs = Category.objects.filter(is_active=True, parent_id=selected_category.id).order_by("name")
    else:
        cat_qs = Category.objects.filter(is_active=True, parent_id__isnull=True).order_by("name")

    categories_out = []
    cat_ids = list(cat_qs.values_list("id", flat=True))
    if cat_ids:
        # Category facet should include children even if products are in deeper descendants.
        product_category_ids = set(
            Product.objects.filter(is_active=True, id__in=product_ids)
            .exclude(category_id__isnull=True)
            .values_list("category_id", flat=True)
        )
        allowed: set[int] = set()
        for child_id in cat_ids:
            desc_ids = set(_descendant_category_ids(root_id=int(child_id)))
            if product_category_ids.intersection(desc_ids):
                allowed.add(int(child_id))

        for c in cat_qs:
            if int(c.id) not in allowed:
                continue
            categories_out.append(
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
            )

    brands_qs = (
        Brand.objects.filter(is_active=True, products__id__in=product_ids)
        .distinct()
        .order_by("name")
    )
    groups_qs = (
        ProductGroup.objects.filter(is_active=True, products__id__in=product_ids)
        .distinct()
        .order_by("name")
    )

    feature_ids = list(
        ProductFeatureValue.objects.filter(product_id__in=product_ids)
        .values_list("feature_id", flat=True)
        .distinct()
    )
    features_qs = (
        Feature.objects.filter(is_active=True, is_filterable=True, id__in=feature_ids)
        .prefetch_related("values")
        .order_by("sort_order", "code")
    )
    features_out: list[FeatureOut] = []
    for f in features_qs:
        used_vals = set(
            ProductFeatureValue.objects.filter(product_id__in=product_ids, feature_id=f.id)
            .values_list("feature_value__value", flat=True)
        )
        vals = [v for v in f.values.all() if v.is_active and v.value in used_vals]
        vals.sort(key=lambda v: (v.sort_order, v.value, v.id))
        features_out.append(
            {
                "id": f.id,
                "code": f.code,
                "name": f.name,
                "values": [{"id": v.id, "value": v.value} for v in vals],
            }
        )

    option_type_ids = list(
        VariantOptionValue.objects.filter(variant__product_id__in=product_ids)
        .values_list("option_type_id", flat=True)
        .distinct()
    )
    option_types_qs = (
        OptionType.objects.filter(is_active=True, id__in=option_type_ids)
        .prefetch_related("values")
        .order_by("sort_order", "code")
    )
    option_types_out: list[OptionTypeOut] = []
    for t in option_types_qs:
        used_codes = set(
            VariantOptionValue.objects.filter(variant__product_id__in=product_ids, option_type_id=t.id)
            .values_list("option_value__code", flat=True)
            .distinct()
        )
        vals = [v for v in t.values.all() if v.is_active and v.code in used_codes]
        vals.sort(key=lambda v: (v.sort_order, v.label, v.id))
        option_types_out.append(
            {
                "id": t.id,
                "code": t.code,
                "name": t.name,
                "display_type": t.display_type,
                "swatch_type": t.swatch_type,
                "values": [{"id": v.id, "code": v.code, "label": v.label} for v in vals],
            }
        )

    return {
        "categories": categories_out,
        "brands": [{"id": b.id, "slug": b.slug, "name": b.name} for b in brands_qs],
        "product_groups": [
            {"id": g.id, "code": g.code, "name": g.name, "description": g.description or ""}
            for g in groups_qs
        ],
        "features": features_out,
        "option_types": option_types_out,
    }


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
    )


@router.get("/products/{slug}", response=ProductDetailOut)
def product_detail(request, slug: str, country_code: str = "LT", channel: str = "normal"):
    country_code = (country_code or "").strip().upper()
    if len(country_code) != 2:
        raise HttpError(400, "Invalid country_code")

    channel = (channel or "normal").strip().lower()
    if channel not in {"normal", "outlet"}:
        raise HttpError(400, "Invalid channel")

    product = (
        Product.objects.filter(slug=slug, is_active=True)
        .select_related("brand", "category", "tax_class")
        .prefetch_related(
            "images",
            "variants",
            "variants__option_values__option_type",
            "variants__option_values__option_value",
            "variants__inventory_items",
        )
        .first()
    )
    if not product:
        raise HttpError(404, "Product not found")

    if not product.tax_class_id:
        raise HttpError(400, "Product has no tax_class assigned")

    try:
        vat_rate = get_vat_rate(country_code=country_code,
                                tax_class=product.tax_class)
    except LookupError:
        raise HttpError(400, "VAT rate not configured for country/tax_class")

    images = list(product.images.all())
    images.sort(key=lambda i: (i.sort_order, i.id))

    variants_qs = [v for v in product.variants.all() if v.is_active]
    variants_qs.sort(key=lambda v: (v.sku, v.id))

    variants: list[VariantOut] = []
    for v in variants_qs:
        inv_all = list(v.inventory_items.all())

        visibility = (
            InventoryItem.OfferVisibility.OUTLET
            if channel == "outlet"
            else InventoryItem.OfferVisibility.NORMAL
        )
        inv = [ii for ii in inv_all if ii.offer_visibility == visibility]

        stock = sum([ii.qty_available for ii in inv]) if inv else 0

        best_offer: InventoryItem | None = None
        inv_available = [ii for ii in inv if ii.qty_available > 0]
        if inv_available:
            inv_available.sort(
                key=lambda ii: (
                    -int(ii.offer_priority or 0),
                    _effective_offer_unit_net(list_unit_net=Decimal(v.price_eur), offer=ii),
                    int(ii.id),
                )
            )
            best_offer = inv_available[0]

        list_unit_net = Decimal(v.price_eur)
        base_unit_net = (
            _effective_offer_unit_net(list_unit_net=list_unit_net, offer=best_offer)
            if best_offer
            else list_unit_net
        )

        is_discounted_offer = bool(
            best_offer
            and (not bool(getattr(best_offer, "never_discount", False)))
            and (
                best_offer.offer_price_override_eur is not None
                or best_offer.offer_discount_percent is not None
            )
        )

        sale_unit_net, _rule = apply_promo_to_unit_net(
            base_unit_net=base_unit_net,
            channel=channel,
            category_id=product.category_id,
            brand_id=product.brand_id,
            product_id=product.id,
            variant_id=v.id,
            customer_group_id=None,
            allow_additional_promotions=bool(getattr(best_offer, "allow_additional_promotions", False)) if best_offer else False,
            is_discounted_offer=is_discounted_offer,
        )

        compare_base = base_unit_net
        disc_pct = _discount_percent(list_unit_net=compare_base, sale_unit_net=sale_unit_net)

        options = list(v.option_values.select_related(
            "option_type", "option_value").all())
        options.sort(key=lambda r: (
            r.option_type.sort_order, r.option_type.code))

        variants.append(
            {
                "id": v.id,
                "sku": v.sku,
                "barcode": v.barcode,
                "name": v.name,
                "is_active": bool(v.is_active),
                "stock_available": int(stock),
                "price": _money_out(currency="EUR", unit_net=sale_unit_net, vat_rate=Decimal(vat_rate)),
                "compare_at_price": (
                    _money_out(currency="EUR", unit_net=compare_base, vat_rate=Decimal(vat_rate))
                    if disc_pct is not None
                    else None
                ),
                "offer_id": (int(best_offer.id) if best_offer else None),
                "offer_label": (best_offer.offer_label if best_offer else ""),
                "condition_grade": (best_offer.condition_grade if best_offer else ""),
                "offer_visibility": (best_offer.offer_visibility if best_offer else ""),
                "discount_percent": disc_pct,
                "options": [
                    {
                        "option_type_code": r.option_type.code,
                        "option_type_name": r.option_type.name,
                        "option_value_code": r.option_value.code,
                        "option_value_label": r.option_value.label,
                    }
                    for r in options
                ],
            }
        )

    return {
        "id": product.id,
        "sku": product.sku,
        "slug": product.slug,
        "name": product.name,
        "description": product.description,
        "is_active": bool(product.is_active),
        "seo_title": getattr(product, "seo_title", "") or "",
        "seo_description": getattr(product, "seo_description", "") or "",
        "seo_keywords": getattr(product, "seo_keywords", "") or "",
        "brand": {
            "id": product.brand.id,
            "slug": product.brand.slug,
            "name": product.brand.name,
        }
        if product.brand
        else None,
        "category": {
            "id": product.category.id,
            "slug": product.category.slug,
            "name": product.category.name,
        }
        if product.category
        else None,
        "images": [
            {
                "avif_url": img.avif_url or None,
                "webp_url": img.webp_url or None,
                "url": img.url,
                "alt_text": img.alt_text,
                "sort_order": img.sort_order,
            }
            for img in images
            if img.url
        ],
        "variants": variants,
    }
