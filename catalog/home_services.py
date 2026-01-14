from __future__ import annotations

from decimal import Decimal

from django.db.models import Case, DecimalField, ExpressionWrapper, F, IntegerField, Min, Q, Sum, Value, When
from django.db.models.functions import Coalesce
from ninja.errors import HttpError

from pricing.services import compute_vat, get_vat_rate
from promotions.services import apply_promo_to_unit_net

from .api_schemas import MoneyOut, ProductListOut
from .models import Brand, Category, InventoryItem, Product, ProductGroup


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


def get_products_for_grid(
    *,
    country_code: str,
    channel: str,
    q: str | None = None,
    category_slug: str | None = None,
    brand_slug: str | None = None,
    group_code: str | None = None,
    feature: str | None = None,
    option: str | None = None,
    sort: str | None = None,
    in_stock_only: bool = False,
    limit: int = 12,
    exclude_product_ids: set[int] | None = None,
    site_id: int | None = None,
) -> list[ProductListOut]:
    return get_products_for_home(
        site_id=site_id,
        country_code=country_code,
        channel=channel,
        q=q,
        category_slug=category_slug,
        brand_slug=brand_slug,
        group_code=group_code,
        feature=feature,
        option=option,
        sort=sort,
        in_stock_only=in_stock_only,
        limit=limit,
        exclude_product_ids=exclude_product_ids,
    )


def _money_out(*, currency: str, unit_net: Decimal, vat_rate: Decimal) -> MoneyOut:
    b = compute_vat(unit_net=Decimal(unit_net), vat_rate=Decimal(vat_rate), qty=1)
    return {
        "currency": currency,
        "net": b.unit_net,
        "vat_rate": b.vat_rate,
        "vat": b.unit_vat,
        "gross": b.unit_gross,
    }


def _discount_percent(*, list_unit_net: Decimal, sale_unit_net: Decimal) -> int | None:
    list_unit_net = Decimal(list_unit_net or 0)
    sale_unit_net = Decimal(sale_unit_net or 0)
    if list_unit_net <= 0:
        return None
    if sale_unit_net >= list_unit_net:
        return None
    pct = int(((list_unit_net - sale_unit_net) / list_unit_net) * Decimal(100))
    return max(0, min(100, pct))


def _descendant_category_ids(*, root_id: int) -> list[int]:
    rows = Category.objects.filter(is_active=True).values("id", "parent_id")
    children: dict[int, list[int]] = {}
    for r in rows:
        pid = int(r.get("parent_id") or 0)
        cid = int(r["id"])
        children.setdefault(pid, []).append(cid)

    out: list[int] = []
    stack = [int(root_id)]
    seen: set[int] = set()
    while stack:
        cur = stack.pop()
        if cur in seen:
            continue
        seen.add(cur)
        out.append(cur)
        for child in children.get(cur, []):
            if child not in seen:
                stack.append(child)
    return out


def get_products_by_slugs_for_grid(
    *,
    site_id: int | None = None,
    country_code: str,
    channel: str,
    product_slugs: list[str],
    in_stock_only: bool = True,
) -> list[ProductListOut]:
    slugs = [(s or "").strip() for s in (product_slugs or [])]
    slugs = [s for s in slugs if s]
    if not slugs:
        return []

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

    min_price_expr = Min("variants__price_eur", filter=Q(variants__is_active=True))

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
        Product.objects.filter(is_active=True, slug__in=slugs)
        .select_related("brand", "category", "tax_class")
        .prefetch_related("images")
        .annotate(_min_variant_price=min_price_expr)
        .annotate(_min_offer_price=min_offer_price_expr)
    )

    qs = qs.annotate(
        _has_stock=Case(
            When(_min_offer_price__isnull=False, then=Value(1)),
            default=Value(0),
            output_field=IntegerField(),
        )
    )

    if channel == "outlet":
        qs = qs.filter(_min_offer_price__isnull=False)

    if in_stock_only:
        qs = qs.filter(_has_stock=1)

    qs = qs.order_by("-_has_stock", "name", "id")

    vat_cache: dict[int, Decimal] = {}

    def vat_rate_for(product: Product) -> Decimal:
        if not product.tax_class_id:
            raise HttpError(400, "Product has no tax_class assigned")
        key = int(product.tax_class_id)
        if key in vat_cache:
            return vat_cache[key]
        try:
            rate = get_vat_rate(country_code=country_code, tax_class=product.tax_class)
        except LookupError:
            raise HttpError(400, "VAT rate not configured for country/tax_class")
        vat_cache[key] = Decimal(rate)
        return vat_cache[key]

    rendered: list[ProductListOut] = []
    for p in qs:
        list_net = Decimal(p._min_variant_price if getattr(p, "_min_variant_price", None) is not None else 0)
        if getattr(p, "_min_offer_price", None) is not None:
            base_net = Decimal(p._min_offer_price)
        else:
            base_net = Decimal(list_net)
        rate = vat_rate_for(p)

        is_discounted_offer = bool(base_net and list_net and base_net < list_net)
        allow_additional_promotions = not is_discounted_offer

        sid = int(site_id or 0)

        sale_net, _rule = apply_promo_to_unit_net(
            base_unit_net=base_net,
            site_id=sid,
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
        discount_percent = _discount_percent(list_unit_net=list_net, sale_unit_net=sale_net)
        if discount_percent is not None:
            compare_at_price = _money_out(currency="EUR", unit_net=list_net, vat_rate=rate)

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

        rendered.append(
            {
                "id": p.id,
                "sku": p.sku,
                "slug": p.slug,
                "name": p.name,
                "is_active": bool(p.is_active),
                "brand": {"id": p.brand.id, "slug": p.brand.slug, "name": p.brand.name} if p.brand else None,
                "category": {"id": p.category.id, "slug": p.category.slug, "name": p.category.name} if p.category else None,
                "images": images_out,
                "price": _money_out(currency="EUR", unit_net=Decimal(sale_net), vat_rate=rate),
                "compare_at_price": compare_at_price,
                "discount_percent": discount_percent,
            }
        )

    order_index = {slug: i for i, slug in enumerate(slugs)}
    rendered.sort(key=lambda p: order_index.get(p.get("slug"), 10_000))
    return rendered


def get_products_for_home(
    *,
    site_id: int | None,
    country_code: str,
    channel: str,
    q: str | None = None,
    category_slug: str | None = None,
    brand_slug: str | None = None,
    group_code: str | None = None,
    feature: str | None = None,
    option: str | None = None,
    sort: str | None = None,
    in_stock_only: bool = False,
    limit: int = 12,
    exclude_product_ids: set[int] | None = None,
) -> list[ProductListOut]:
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

    min_price_expr = Min("variants__price_eur", filter=Q(variants__is_active=True))

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
    )

    qs = qs.annotate(
        _has_stock=Case(
            When(_min_offer_price__isnull=False, then=Value(1)),
            default=Value(0),
            output_field=IntegerField(),
        )
    )

    if exclude_product_ids:
        qs = qs.exclude(id__in=list(exclude_product_ids))

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

    if in_stock_only:
        qs = qs.filter(_has_stock=1)

    sort_v = (sort or "").strip().lower()
    if sort_v in {"price", "-price"}:
        qs = qs.annotate(_sort_price=Coalesce("_min_offer_price", "_min_variant_price"))
        qs = (
            qs.order_by("-_has_stock", "_sort_price", "name", "id")
            if sort_v == "price"
            else qs.order_by("-_has_stock", "-_sort_price", "name", "id")
        )
    elif sort_v in {"created", "created_at", "-created", "-created_at"}:
        if sort_v.startswith("-"):
            qs = qs.order_by("-_has_stock", "-created_at", "-id")
        else:
            qs = qs.order_by("-_has_stock", "created_at", "id")
    elif sort_v in {"discounted", "-discounted"}:
        qs = qs.annotate(
            _is_discounted=Case(
                When(_min_offer_price__isnull=False, _min_offer_price__lt=F("_min_variant_price"), then=Value(1)),
                default=Value(0),
                output_field=IntegerField(),
            )
        )
        qs = (
            qs.order_by("-_has_stock", "-_is_discounted", "name", "id")
            if sort_v == "discounted"
            else qs.order_by("-_has_stock", "_is_discounted", "name", "id")
        )
    elif sort_v in {"best_selling", "-best_selling"}:
        from checkout.models import Order

        qs = qs.annotate(
            _sold_qty=Coalesce(
                Sum(
                    "variants__order_lines__qty",
                    filter=Q(variants__order_lines__order__status=Order.Status.PAID),
                    output_field=IntegerField(),
                ),
                Value(0),
            )
        )
        qs = (
            qs.order_by("-_has_stock", "-_sold_qty", "name", "id")
            if sort_v == "best_selling"
            else qs.order_by("-_has_stock", "_sold_qty", "name", "id")
        )
    else:
        qs = qs.order_by("-_has_stock", "name", "id")

    qs = qs[: max(0, int(limit or 0))]

    vat_cache: dict[int, Decimal] = {}

    def vat_rate_for(product: Product) -> Decimal:
        if not product.tax_class_id:
            raise HttpError(400, "Product has no tax_class assigned")
        key = int(product.tax_class_id)
        if key in vat_cache:
            return vat_cache[key]
        try:
            rate = get_vat_rate(country_code=country_code, tax_class=product.tax_class)
        except LookupError:
            raise HttpError(400, "VAT rate not configured for country/tax_class")
        vat_cache[key] = Decimal(rate)
        return vat_cache[key]

    out: list[ProductListOut] = []
    for p in qs:
        list_net = Decimal(p._min_variant_price if getattr(p, "_min_variant_price", None) is not None else 0)
        if getattr(p, "_min_offer_price", None) is not None:
            base_net = Decimal(p._min_offer_price)
        else:
            base_net = Decimal(list_net)
        rate = vat_rate_for(p)

        is_discounted_offer = bool(base_net and list_net and base_net < list_net)
        allow_additional_promotions = not is_discounted_offer

        sid = int(site_id or 0)

        sale_net, _rule = apply_promo_to_unit_net(
            base_unit_net=base_net,
            site_id=sid,
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
        discount_percent = _discount_percent(list_unit_net=list_net, sale_unit_net=sale_net)
        if discount_percent is not None:
            compare_at_price = _money_out(currency="EUR", unit_net=list_net, vat_rate=rate)

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
                "brand": {"id": p.brand.id, "slug": p.brand.slug, "name": p.brand.name} if p.brand else None,
                "category": {"id": p.category.id, "slug": p.category.slug, "name": p.category.name} if p.category else None,
                "images": images_out,
                "price": _money_out(currency="EUR", unit_net=Decimal(sale_net), vat_rate=rate),
                "compare_at_price": compare_at_price,
                "discount_percent": discount_percent,
            }
        )

    return out
