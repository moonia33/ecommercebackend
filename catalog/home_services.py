from __future__ import annotations

from decimal import Decimal

from django.db.models import Case, DecimalField, ExpressionWrapper, F, IntegerField, Max, Min, Q, Sum, Value, When
from django.db.models.functions import Coalesce
from ninja.errors import HttpError

from .localization import (
    resolve_brand_by_slug,
    resolve_category_by_slug,
    resolve_product_by_slug,
)
from .api_schemas import ProductListOut
from .models import InventoryItem, Product, ProductGroup
from .services.catalog_core import descendant_category_ids, parse_pairs
from .services.product_listing import serialize_products_listing


def get_products_by_slugs_for_grid(
    *,
    country_code: str,
    channel: str,
    product_slugs: list[str],
    language_code: str | None = None,
    in_stock_only: bool = True,
) -> list[ProductListOut]:
    slugs = [(s or "").strip() for s in (product_slugs or [])]
    slugs = [s for s in slugs if s]
    if not slugs:
        return []

    resolved_ids: list[int] = []
    seen_ids: set[int] = set()
    for slug in slugs:
        p = resolve_product_by_slug(slug=slug, language_code=language_code, is_active=True)
        if p is None or int(p.id) in seen_ids:
            continue
        resolved_ids.append(int(p.id))
        seen_ids.add(int(p.id))
    if not resolved_ids:
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
    max_price_expr = Max("variants__price_eur", filter=Q(variants__is_active=True))

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
        Product.objects.filter(is_active=True, id__in=resolved_ids)
        .select_related("brand", "category", "tax_class")
        .prefetch_related("images", "translations", "brand__translations", "category__translations")
        .annotate(_min_variant_price=min_price_expr)
        .annotate(_max_variant_price=max_price_expr)
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

    rendered = serialize_products_listing(
        qs=qs,
        country_code=country_code,
        channel=channel,
        language_code=language_code,
    )

    order_index_by_id = {pid: i for i, pid in enumerate(resolved_ids)}
    rendered.sort(key=lambda p: order_index_by_id.get(int(p.get("id") or 0), 10_000))
    return rendered


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
    language_code: str | None = None,
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

    base_annotated = (
        Product.objects.filter(is_active=True)
        .select_related("brand", "category", "tax_class")
        .prefetch_related("images", "translations", "brand__translations", "category__translations")
        .annotate(_min_variant_price=min_price_expr)
        .annotate(_max_variant_price=Max("variants__price_eur", filter=Q(variants__is_active=True)))
        .annotate(_min_offer_price=min_offer_price_expr)
    )

    # Pirminis kelias — Typesense browse (vienas indeksas vietoj viso
    # katalogo agregavimo). Pinned/exclude atmetami čia. None → ORM žemiau.
    if limit and int(limit) > 0:
        from catalog.api.products import _engine_browse

        excluded = {int(i) for i in (exclude_product_ids or set())}
        engine = _engine_browse(
            q=q,
            shop_id=None,
            channel=channel,
            category_slug=category_slug,
            brand_slug=brand_slug,
            group_code=group_code,
            feature=feature,
            option=option,
            range_filter=None,
            sort=sort,
            in_stock_only=in_stock_only,
            page=1,
            page_size=min(250, int(limit) + len(excluded)),
            language_code=language_code,
        )
        if engine is not None:
            ids, _found = engine
            ids = [i for i in ids if i not in excluded][: int(limit)]
            if ids:
                from catalog.api.products import _ordered_listing_qs

                qs_page = _ordered_listing_qs(ids=ids, qs_base=base_annotated)
                return serialize_products_listing(
                    qs=qs_page,
                    country_code=country_code,
                    channel=channel,
                    language_code=language_code,
                )
            return []

    qs = base_annotated.annotate(
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
        c = resolve_category_by_slug(slug=category_slug, language_code=language_code, is_active=True)
        if not c:
            raise HttpError(404, "Category not found")
        ids = descendant_category_ids(root_id=int(c.id))
        qs = qs.filter(category_id__in=ids)

    if brand_slug:
        b = resolve_brand_by_slug(slug=brand_slug, language_code=language_code, is_active=True)
        if not b:
            raise HttpError(404, "Brand not found")
        qs = qs.filter(brand_id=b.id)

    if group_code:
        g = ProductGroup.objects.filter(code=group_code, is_active=True).first()
        if not g:
            raise HttpError(404, "Product group not found")
        qs = qs.filter(group_id=g.id)

    for f_code, f_val in parse_pairs(feature):
        qs = qs.filter(
            feature_values__feature__code=f_code,
            feature_values__feature_value__value=f_val,
        )

    for o_type, o_val in parse_pairs(option):
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

    return serialize_products_listing(
        qs=qs,
        country_code=country_code,
        channel=channel,
        language_code=language_code,
    )
