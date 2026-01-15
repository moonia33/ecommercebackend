from __future__ import annotations

from decimal import Decimal

from django.conf import settings
from django.db.models import Case, DecimalField, ExpressionWrapper, F, Min, Q, Value, When
from ninja import Router, Schema
from ninja.errors import HttpError

from catalog.api_schemas import ProductListOut
from catalog.models import InventoryItem, Product, Variant
from promotions.services import apply_promo_to_unit_net
from pricing.services import get_vat_rate

from .models import RecommendationSetItem


router = Router(tags=["recommendations"])


class ProductRecommendationsOut(Schema):
    blocks: list["RecommendationBlockOut"]


class RecommendationBlockOut(Schema):
    key: str
    name: str
    items: list[ProductListOut]


def _limit(name: str, default: int) -> int:
    try:
        v = int(getattr(settings, name, default))
        if v < 0:
            return default
        return v
    except Exception:
        return default


def _get_request_site_id(request) -> int | None:
    site = getattr(request, "site", None)
    if site is None:
        return None
    try:
        sid = getattr(site, "id", None)
        return int(sid) if sid is not None else None
    except Exception:
        return None


def _apply_site_assortment_to_product_qs(*, qs, site_id: int | None, selected_category_id: int | None):
    from catalog.api import _apply_site_assortment_to_product_qs as _apply

    return _apply(qs=qs, site_id=site_id, selected_category_id=selected_category_id)


def _money_out(*, currency: str, unit_net: Decimal, vat_rate: Decimal):
    from catalog.api import _money_out as _m

    return _m(currency=currency, unit_net=unit_net, vat_rate=vat_rate)


def _discount_percent(*, list_unit_net: Decimal, sale_unit_net: Decimal):
    from catalog.api import _discount_percent as _d

    return _d(list_unit_net=list_unit_net, sale_unit_net=sale_unit_net)


def _product_cards(
    *,
    request,
    products: list[Product],
    country_code: str,
    channel: str,
) -> list[ProductListOut]:
    if not products:
        return []

    country_code = (country_code or "").strip().upper()
    if len(country_code) != 2:
        raise HttpError(400, "Invalid country_code")

    channel = (channel or "normal").strip().lower()
    if channel not in {"normal", "outlet"}:
        raise HttpError(400, "Invalid channel")

    site_id = _get_request_site_id(request)
    if site_id is None:
        raise HttpError(400, "Site is not resolved")

    visibility = (
        InventoryItem.OfferVisibility.OUTLET
        if channel == "outlet"
        else InventoryItem.OfferVisibility.NORMAL
    )

    product_ids = [int(p.id) for p in products]

    min_price_expr = Min("variants__price_eur", filter=Q(variants__is_active=True))
    offer_price_expr = Case(
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

    products_qs = (
        Product.objects.filter(is_active=True, id__in=product_ids)
        .select_related("brand", "category", "tax_class")
        .prefetch_related("images")
        .annotate(_min_variant_price=min_price_expr)
        .annotate(_min_offer_price=min_offer_price_expr)
    )
    products_qs = _apply_site_assortment_to_product_qs(qs=products_qs, site_id=site_id, selected_category_id=None)

    product_by_id = {int(p.id): p for p in products_qs}

    vat_cache: dict[int, Decimal] = {}

    def vat_rate_for(p: Product) -> Decimal:
        if not p.tax_class_id:
            raise HttpError(400, "Product has no tax_class assigned")
        key = int(p.tax_class_id)
        if key in vat_cache:
            return vat_cache[key]
        try:
            rate = get_vat_rate(country_code=country_code, tax_class=p.tax_class)
        except LookupError:
            raise HttpError(400, "VAT rate not configured for country/tax_class")
        vat_cache[key] = Decimal(rate)
        return vat_cache[key]

    out: list[ProductListOut] = []
    for pid in product_ids:
        p = product_by_id.get(int(pid))
        if p is None:
            continue

        list_net = Decimal(getattr(p, "_min_variant_price", None) or 0)
        if getattr(p, "_min_offer_price", None) is not None:
            base_net = Decimal(p._min_offer_price)
        else:
            base_net = Decimal(list_net)
        rate = vat_rate_for(p)

        is_discounted_offer = bool(base_net and list_net and base_net < list_net)
        allow_additional_promotions = not is_discounted_offer

        sale_net, _rule = apply_promo_to_unit_net(
            base_unit_net=base_net,
            site_id=int(site_id),
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
            list_avif = getattr(img, "listing_avif_url", None) or None
            list_webp = getattr(img, "listing_webp_url", None) or None
            list_url = list_avif or list_webp or img.url
            images_out.append(
                {
                    "avif_url": list_avif or (getattr(img, "avif_url", None) or None),
                    "webp_url": list_webp or (getattr(img, "webp_url", None) or None),
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


def _manual_recommendations(*, request, product: Product, limit: int = 12) -> list[Product]:
    site_id = _get_request_site_id(request)
    if site_id is None:
        return []

    item_qs = RecommendationSetItem.objects.filter(set__is_active=True, product_id=int(product.id))
    set_ids = list(item_qs.values_list("set_id", flat=True))
    if not set_ids:
        return []

    other_items = (
        RecommendationSetItem.objects.filter(set_id__in=set_ids, set__is_active=True)
        .exclude(product_id=int(product.id))
        .select_related("product")
        .order_by("sort_order", "id")
    )

    seen: set[int] = set()
    targets: list[Product] = []
    for it in other_items:
        pid = int(it.product_id)
        if pid in seen:
            continue
        seen.add(pid)
        targets.append(it.product)
        if len(targets) >= int(limit):
            break

    targets_qs = Product.objects.filter(id__in=[p.id for p in targets], is_active=True)
    targets_qs = _apply_site_assortment_to_product_qs(qs=targets_qs, site_id=site_id, selected_category_id=None)
    allowed = {int(p.id): p for p in targets_qs}

    out = [allowed.get(int(p.id)) for p in targets]
    out = [p for p in out if p is not None]
    return out


def _manual_recommendation_blocks(*, request, product: Product) -> list[tuple[str, str, list[Product]]]:
    site_id = _get_request_site_id(request)
    if site_id is None:
        return []

    manual_limit = _limit("RECO_MANUAL_LIMIT", 12)
    if manual_limit <= 0:
        return []

    item_qs = RecommendationSetItem.objects.filter(set__is_active=True, product_id=int(product.id)).select_related(
        "set",
        "product",
    )
    set_ids = list(item_qs.values_list("set_id", flat=True))
    if not set_ids:
        return []

    other_items = (
        RecommendationSetItem.objects.filter(set_id__in=set_ids, set__is_active=True)
        .exclude(product_id=int(product.id))
        .select_related("set", "product")
        .order_by("set__kind", "sort_order", "id")
    )

    kind_to_key_name: dict[str, tuple[str, str]] = {
        "complements": ("manual_complements", "Dažnai perkama kartu"),
        "upsell": ("manual_upsell", "Rekomenduojame rinktis geresnį"),
        "similar": ("manual_similar", "Panašios prekės"),
    }

    kind_to_ids: dict[str, list[int]] = {}
    kind_to_seen: dict[str, set[int]] = {}
    for it in other_items:
        kind = str(getattr(it.set, "kind", "complements") or "complements")
        pid = int(it.product_id)
        if pid == int(product.id):
            continue
        seen = kind_to_seen.setdefault(kind, set())
        if pid in seen:
            continue
        seen.add(pid)

        bucket = kind_to_ids.setdefault(kind, [])
        if len(bucket) >= int(manual_limit):
            continue
        bucket.append(pid)

    if not kind_to_ids:
        return []

    all_ids = [pid for ids in kind_to_ids.values() for pid in ids]
    targets_qs = Product.objects.filter(id__in=all_ids, is_active=True)
    targets_qs = _apply_site_assortment_to_product_qs(qs=targets_qs, site_id=site_id, selected_category_id=None)
    allowed = {int(p.id): p for p in targets_qs}

    blocks: list[tuple[str, str, list[Product]]] = []
    for kind in ["complements", "upsell", "similar"]:
        ids = kind_to_ids.get(kind) or []
        if not ids:
            continue
        key, name = kind_to_key_name.get(kind, (f"manual_{kind}", kind))
        prods = [allowed.get(int(pid)) for pid in ids]
        prods = [p for p in prods if p is not None]
        if not prods:
            continue
        blocks.append((key, name, prods))

    return blocks


def _auto_recommendations(
    *,
    request,
    product: Product,
    exclude_ids: set[int],
    limit: int = 12,
) -> list[Product]:
    site_id = _get_request_site_id(request)
    if site_id is None:
        return []

    qs = Product.objects.filter(is_active=True).exclude(id__in=list(exclude_ids | {int(product.id)}))

    if getattr(product, "group_id", None):
        qs = qs.filter(group_id=int(product.group_id))
    else:
        if product.brand_id:
            qs = qs.filter(brand_id=int(product.brand_id))
        if product.category_id:
            qs = qs.filter(category_id=int(product.category_id))

    qs = _apply_site_assortment_to_product_qs(qs=qs, site_id=site_id, selected_category_id=None)

    return list(qs.select_related("brand", "category", "tax_class").prefetch_related("images")[: int(limit)])


def _upsell_recommendations(
    *,
    request,
    product: Product,
    exclude_ids: set[int],
    limit: int = 12,
) -> list[Product]:
    site_id = _get_request_site_id(request)
    if site_id is None:
        return []

    if limit <= 0:
        return []

    base_price = Variant.objects.filter(product_id=int(product.id), is_active=True).aggregate(m=Min("price_eur")).get("m")
    if base_price is None:
        return []

    lower = Decimal(str(base_price)) * Decimal("1.15")
    upper = Decimal(str(base_price)) * Decimal("1.80")

    qs = Product.objects.filter(is_active=True).exclude(id__in=list(exclude_ids | {int(product.id)}))
    if product.category_id:
        qs = qs.filter(category_id=int(product.category_id))
    if product.brand_id:
        qs = qs.filter(brand_id=int(product.brand_id))

    qs = qs.annotate(_min_variant_price=Min("variants__price_eur", filter=Q(variants__is_active=True)))
    qs = qs.filter(_min_variant_price__isnull=False, _min_variant_price__gte=lower, _min_variant_price__lte=upper)

    qs = _apply_site_assortment_to_product_qs(qs=qs, site_id=site_id, selected_category_id=None)

    return list(qs.select_related("brand", "category", "tax_class").prefetch_related("images")[: int(limit)])


@router.get("/products/{slug}", response=ProductRecommendationsOut)
def product_recommendations(
    request,
    slug: str,
    country_code: str = "LT",
    channel: str = "normal",
):
    site_id = _get_request_site_id(request)

    product_qs = Product.objects.filter(slug=slug, is_active=True)
    product_qs = _apply_site_assortment_to_product_qs(qs=product_qs, site_id=site_id, selected_category_id=None)

    product = product_qs.select_related("brand", "category", "tax_class").first()
    if not product:
        raise HttpError(404, "Product not found")

    blocks: list[RecommendationBlockOut] = []

    manual_blocks = _manual_recommendation_blocks(request=request, product=product)
    manual_exclude_ids: set[int] = set()
    for key, name, prods in manual_blocks:
        manual_exclude_ids.update({int(p.id) for p in prods})
        blocks.append(
            {
                "key": str(key),
                "name": str(name),
                "items": _product_cards(request=request, products=prods, country_code=country_code, channel=channel),
            }
        )

    cross_sell_limit = _limit("RECO_CROSS_SELL_LIMIT", 12)
    upsell_limit = _limit("RECO_UPSELL_LIMIT", 8)

    cross_sell_products = _auto_recommendations(
        request=request,
        product=product,
        exclude_ids=set(manual_exclude_ids),
        limit=cross_sell_limit,
    )
    if cross_sell_products:
        blocks.append(
            {
                "key": "auto_cross_sell",
                "name": "Cross-sell",
                "items": _product_cards(
                    request=request,
                    products=cross_sell_products,
                    country_code=country_code,
                    channel=channel,
                ),
            }
        )

    upsell_products = _upsell_recommendations(
        request=request,
        product=product,
        exclude_ids=set(manual_exclude_ids) | {int(p.id) for p in cross_sell_products},
        limit=upsell_limit,
    )
    if upsell_products:
        blocks.append(
            {
                "key": "auto_upsell",
                "name": "Upsell",
                "items": _product_cards(
                    request=request,
                    products=upsell_products,
                    country_code=country_code,
                    channel=channel,
                ),
            }
        )

    return {"blocks": blocks}
