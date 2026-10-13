from __future__ import annotations

from decimal import Decimal

from django.utils import timezone
from ninja.errors import HttpError

from catalog.content_blocks import get_content_blocks_for_product
from catalog.localization import (
    localize_brand,
    localize_category,
    localize_feature,
    localize_feature_value,
    localize_option_type,
    localize_option_value,
    localize_product,
)
from catalog.models import InventoryItem
from catalog.shop_pricing import resolve_shop_variant_price
from catalog.services.money import discount_percent, effective_offer_unit_net, money_out
from pricing.services import get_vat_rate
from promotions.services import apply_promo_to_unit_net
from shipping.services import estimate_delivery_window


def build_product_detail_payload(
    *,
    product,
    shop_id: int | None,
    country_code: str,
    channel: str,
    language_code: str | None,
):
    product_loc = localize_product(product, language_code=language_code)
    brand_loc = localize_brand(product.brand, language_code=language_code) if product.brand else None
    category_loc = localize_category(product.category, language_code=language_code) if product.category else None

    if not product.tax_class_id:
        raise HttpError(400, "Product has no tax_class assigned")

    try:
        vat_rate = get_vat_rate(country_code=country_code, tax_class=product.tax_class)
    except LookupError:
        raise HttpError(400, "VAT rate not configured for country/tax_class")

    images = list(product.images.all())
    images.sort(key=lambda i: (i.sort_order, i.id))

    variants_qs = [v for v in product.variants.all() if v.is_active]
    variants_qs.sort(key=lambda v: (v.sku, v.id))

    variants = []
    delivery_window_out = None
    best_delivery_min = None
    for v in variants_qs:
        inv_all = list(v.inventory_items.all())
        visibility = InventoryItem.OfferVisibility.OUTLET if channel == "outlet" else InventoryItem.OfferVisibility.NORMAL
        inv = [ii for ii in inv_all if ii.offer_visibility == visibility]
        stock = sum([ii.qty_available for ii in inv]) if inv else 0

        best_offer = None
        inv_available = [ii for ii in inv if ii.qty_available > 0]
        if inv_available:
            inv_available.sort(
                key=lambda ii: (
                    -int(ii.offer_priority or 0),
                    effective_offer_unit_net(list_unit_net=Decimal(v.price_eur), offer=ii),
                    int(ii.id),
                )
            )
            best_offer = inv_available[0]

        if best_offer and product is not None:
            dw = estimate_delivery_window(
                now=timezone.now(),
                country_code=country_code,
                channel=channel,
                warehouse_id=int(best_offer.warehouse_id) if best_offer.warehouse_id else None,
                product_id=int(product.id),
                brand_id=int(product.brand_id) if product.brand_id else None,
                category_id=int(product.category_id) if product.category_id else None,
                product_group_id=int(product.group_id) if getattr(product, "group_id", None) else None,
            )
            if dw is not None and (best_delivery_min is None or dw.min_date < best_delivery_min):
                best_delivery_min = dw.min_date
                delivery_window_out = {
                    "min_date": dw.min_date.isoformat(),
                    "max_date": dw.max_date.isoformat(),
                    "kind": dw.kind,
                    "rule_code": dw.rule_code,
                    "source": dw.source,
                }

        price_res = resolve_shop_variant_price(
            variant=v,
            shop_id=shop_id,
            channel_type="retail",
            customer_group_id=None,
        )
        list_unit_net = Decimal(price_res.unit_net)
        base_unit_net = effective_offer_unit_net(list_unit_net=list_unit_net, offer=best_offer) if best_offer else list_unit_net

        is_discounted_offer = bool(
            best_offer
            and (not bool(getattr(best_offer, "never_discount", False)))
            and (best_offer.offer_price_override_eur is not None or best_offer.offer_discount_percent is not None)
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

        disc_pct = discount_percent(list_unit_net=list_unit_net, sale_unit_net=sale_unit_net)
        options = list(v.option_values.select_related("option_type", "option_value").all())
        options.sort(key=lambda r: (r.option_type.sort_order, r.option_type.code))

        variants.append(
            {
                "id": v.id,
                "sku": v.sku,
                "barcode": v.barcode,
                "name": v.name,
                "is_active": bool(v.is_active),
                "stock_available": int(stock),
                "price": money_out(currency="EUR", unit_net=sale_unit_net, vat_rate=Decimal(vat_rate)),
                "compare_at_price": (money_out(currency="EUR", unit_net=list_unit_net, vat_rate=Decimal(vat_rate)) if disc_pct is not None else None),
                "offer_id": (int(best_offer.id) if best_offer else None),
                "offer_label": (best_offer.offer_label if best_offer else ""),
                "condition_grade": (best_offer.condition_grade if best_offer else ""),
                "offer_visibility": (best_offer.offer_visibility if best_offer else ""),
                "discount_percent": disc_pct,
                "options": [
                    {
                        "option_type_code": r.option_type.code,
                        "option_type_name": localize_option_type(r.option_type, language_code=language_code),
                        "option_value_code": r.option_value.code,
                        "option_value_label": localize_option_value(r.option_value, language_code=language_code),
                    }
                    for r in options
                ],
            }
        )

    feature_rows = list(product.feature_values.select_related("feature", "feature_value").all())
    feature_rows.sort(
        key=lambda r: (
            r.feature.sort_order,
            r.feature.code,
            r.feature_value.sort_order,
            r.feature_value.value,
            r.id,
        )
    )
    features_out = [
        {
            "feature_id": r.feature_id,
            "feature_code": r.feature.code,
            "feature_name": localize_feature(r.feature, language_code=language_code),
            "value_id": r.feature_value_id,
            "value": r.feature_value.value,
            "value_label": localize_feature_value(r.feature_value, language_code=language_code),
        }
        for r in feature_rows
    ]

    content_blocks = get_content_blocks_for_product(
        product_id=int(product.id),
        placement="product_detail",
        channel=channel,
        brand_id=int(product.brand_id) if product.brand_id else None,
        category_id=int(product.category_id) if product.category_id else None,
        product_group_id=int(product.group_id) if getattr(product, "group_id", None) else None,
        language_code=language_code,
        now=timezone.now().date(),
    )

    return {
        "id": product.id,
        "sku": product.sku,
        "slug": product_loc["slug"],
        "name": product_loc["name"],
        "description": product_loc["description"],
        "is_active": bool(product.is_active),
        "seo_title": product_loc["seo_title"],
        "seo_description": product_loc["seo_description"],
        "seo_keywords": product_loc["seo_keywords"],
        "brand": {"id": product.brand.id, "slug": brand_loc["slug"], "name": brand_loc["name"]} if product.brand and brand_loc else None,
        "category": {"id": product.category.id, "slug": category_loc["slug"], "name": category_loc["name"]} if product.category and category_loc else None,
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
        "features": features_out,
        "delivery_window": delivery_window_out,
        "content_blocks": [
            {
                "key": b.key,
                "title": b.title,
                "placement": b.placement,
                "type": b.type,
                "payload": b.payload,
            }
            for b in content_blocks
        ],
        "variants": variants,
    }
