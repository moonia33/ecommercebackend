from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from django.db.models import Case, DecimalField, F, IntegerField, Q, Sum, Value, When
from django.utils import timezone
from django.db.models.functions import Coalesce
from ninja.errors import HttpError

from catalog.localization import localize_brand, localize_category, localize_product
from catalog.api_schemas import ProductListOut
from catalog.models import Product, ProductFeatureValue, ProductVideo
from catalog.services.image_out import image_out
from catalog.services.money import discount_percent, money_out
from pricing.services import get_vat_rate
from promotions.services import _MISSING, apply_promo_to_unit_net, load_active_promo_rules


def _rules_need_feature_ctx(rules: list) -> bool:
    """Ar aktyviose promo taisyklėse yra feature_value sąlygų —
    tik tada verta bulk-fetch'inti ProductFeatureValue."""
    for r in rules or []:
        for g in list(getattr(r, "condition_groups", []).all()):
            for c in list(g.conditions.all()):
                if str(c.kind) == "feature_value":
                    return True
    return False


def apply_products_sorting(*, qs, sort: str | None):
    sort_v = (sort or "").strip().lower()
    if sort_v in {"price", "-price"}:
        qs = qs.annotate(_sort_price=Coalesce("_min_offer_price", "_min_variant_price"))
        return (
            qs.order_by("-_has_stock", "_sort_price", "name", "id")
            if sort_v == "price"
            else qs.order_by("-_has_stock", "-_sort_price", "name", "id")
        )

    if sort_v in {"created", "created_at", "-created", "-created_at"}:
        if sort_v.startswith("-"):
            return qs.order_by("-_has_stock", "-created_at", "-id")
        return qs.order_by("-_has_stock", "created_at", "id")

    if sort_v in {"discounted", "-discounted"}:
        qs = qs.annotate(
            _is_discounted=Case(
                When(_min_offer_price__isnull=False, _min_offer_price__lt=F("_min_variant_price"), then=Value(1)),
                default=Value(0),
                output_field=IntegerField(),
            )
        )
        return (
            qs.order_by("-_has_stock", "-_is_discounted", "name", "id")
            if sort_v == "discounted"
            else qs.order_by("-_has_stock", "_is_discounted", "name", "id")
        )

    if sort_v in {"best_selling", "-best_selling"}:
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
        return (
            qs.order_by("-_has_stock", "-_sold_qty", "name", "id")
            if sort_v == "best_selling"
            else qs.order_by("-_has_stock", "_sold_qty", "name", "id")
        )

    return qs.order_by("-_has_stock", "name", "id")


def serialize_products_listing(
    *,
    qs,
    country_code: str,
    channel: str,
    shop_id: int | None = None,
    language_code: str | None = None,
) -> list[ProductListOut]:
    products = list(qs)
    video_product_ids = (
        set(
            ProductVideo.objects.filter(product_id__in=[p.id for p in products]).values_list(
                "product_id", flat=True
            )
        )
        if products
        else set()
    )

    new_cutoff = timezone.now() - timedelta(days=30)

    def labels_for(product: Product) -> list[str]:
        labels: list[str] = []
        if product.created_at and product.created_at >= new_cutoff:
            labels.append("new")
        return labels

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

    promo_rules: list = []
    promo_ctx: dict = {"cat_tree": {}}
    if products:
        promo_rules = load_active_promo_rules(channel=channel, shop_id=shop_id)
        # Bulk prefetch promo ctx visam batch'ui — vietoj per-product N+1.
        if _rules_need_feature_ctx(promo_rules):
            fv_map: dict[int, set[int]] = {}
            for pid, fvid in ProductFeatureValue.objects.filter(
                product_id__in=[p.id for p in products]
            ).values_list("product_id", "feature_value_id"):
                fv_map.setdefault(int(pid), set()).add(int(fvid))
            promo_ctx["feature_value_ids"] = fv_map

    out: list[ProductListOut] = []
    for p in products:
        product_loc = localize_product(p, language_code=language_code)
        brand_loc = localize_brand(p.brand, language_code=language_code) if p.brand else None
        category_loc = localize_category(p.category, language_code=language_code) if p.category else None

        list_net = Decimal(p._min_variant_price if getattr(p, "_min_variant_price", None) is not None else 0)
        base_net = Decimal(p._min_offer_price) if getattr(p, "_min_offer_price", None) is not None else Decimal(list_net)
        rate = vat_rate_for(p)

        is_discounted_offer = bool(base_net and list_net and base_net < list_net)
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
            rules=promo_rules,
            product_group_id=p.group_id,
            feature_value_ids=(promo_ctx["feature_value_ids"].get(p.id) or set())
            if "feature_value_ids" in promo_ctx
            else _MISSING,
            cat_tree=promo_ctx["cat_tree"],
        )

        compare_at_price = None
        disc_pct = discount_percent(list_unit_net=list_net, sale_unit_net=sale_net)
        if disc_pct is not None:
            compare_at_price = money_out(currency="EUR", unit_net=list_net, vat_rate=rate)

        price_to = None
        max_variant_net = getattr(p, "_max_variant_price", None)
        if max_variant_net is not None and Decimal(max_variant_net) > list_net:
            price_to = money_out(
                currency="EUR", unit_net=Decimal(max_variant_net), vat_rate=rate
            )

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
                "is_part": bool(p.is_part),
                "product_group_code": p.product_group_code or "",
                "brand": {"id": p.brand.id, "slug": brand_loc["slug"], "name": brand_loc["name"]} if p.brand and brand_loc else None,
                "category": {"id": p.category.id, "slug": category_loc["slug"], "name": category_loc["name"]} if p.category and category_loc else None,
                "images": images_out,
                "price": money_out(currency="EUR", unit_net=Decimal(sale_net), vat_rate=rate),
                "price_to": price_to,
                "compare_at_price": compare_at_price,
                "discount_percent": disc_pct,
                "has_video": p.id in video_product_ids,
                "labels": labels_for(p),
            }
        )
    return out
