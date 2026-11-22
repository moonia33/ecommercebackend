"""Omnibus (ES 2019/2161): kainų istorija ir mažiausia kaina per 30 d. iki nuolaidos."""
from __future__ import annotations

import logging
from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.db import transaction
from django.db.models import Min
from django.utils import timezone

from catalog.models import InventoryItem, ProductFeatureValue, ShopPriceList, Variant, VariantPriceHistory
from catalog.services.money import discount_percent, effective_offer_unit_net
from catalog.shop_pricing import resolve_shop_variant_price
from promotions.services import _MISSING, apply_promo_to_unit_net, load_active_promo_rules

logger = logging.getLogger(__name__)

CHANNELS = ("normal", "outlet")
PRIOR_WINDOW = timedelta(days=30)
BATCH = 2000
CENT = Decimal("0.01")


def _rules_need_feature_ctx(rules: list) -> bool:
    from catalog.services.product_listing import _rules_need_feature_ctx as need

    return need(rules)


def compute_sale_nets(*, shop_id: int, channel: str) -> dict[int, Decimal]:
    """Galutinė vieša variant'o kaina be PVM – ta pati logika kaip PDP."""
    rules = load_active_promo_rules(channel=channel, shop_id=shop_id)
    need_fv = _rules_need_feature_ctx(rules)
    has_lists = ShopPriceList.objects.filter(shop_id=shop_id, is_active=True, channel_type="retail").exists()
    visibility = (
        InventoryItem.OfferVisibility.OUTLET if channel == "outlet" else InventoryItem.OfferVisibility.NORMAL
    )
    cat_tree: dict = {}
    out: dict[int, Decimal] = {}
    from catalog.models import Product
    from catalog.shop_scope import apply_shop_product_scope

    visible = apply_shop_product_scope(qs=Product.objects.filter(is_active=True), shop_id=shop_id)
    base_qs = (
        Variant.objects.filter(is_active=True, product__in=visible.values("id"))
        .select_related("product")
        .prefetch_related("inventory_items")
        .order_by("id")
    )
    last_id = 0
    while True:
        variants = list(base_qs.filter(id__gt=last_id)[:BATCH])
        if not variants:
            break
        last_id = variants[-1].id
        fv_map: dict[int, set[int]] = {}
        if need_fv:
            for pid, fvid in ProductFeatureValue.objects.filter(
                product_id__in={v.product_id for v in variants}
            ).values_list("product_id", "feature_value_id"):
                fv_map.setdefault(int(pid), set()).add(int(fvid))
        for v in variants:
            p = v.product
            list_net = (
                Decimal(resolve_shop_variant_price(variant=v, shop_id=shop_id).unit_net)
                if has_lists
                else Decimal(v.price_eur)
            )
            offers = [
                ii for ii in v.inventory_items.all()
                if ii.offer_visibility == visibility and ii.qty_available > 0
            ]
            best = None
            if offers:
                offers.sort(
                    key=lambda ii: (
                        -int(ii.offer_priority or 0),
                        effective_offer_unit_net(list_unit_net=Decimal(v.price_eur), offer=ii),
                        int(ii.id),
                    )
                )
                best = offers[0]
            base_net = effective_offer_unit_net(list_unit_net=list_net, offer=best) if best else list_net
            is_discounted_offer = bool(
                best
                and not best.never_discount
                and (best.offer_price_override_eur is not None or best.offer_discount_percent is not None)
            )
            sale_net, _rule = apply_promo_to_unit_net(
                base_unit_net=base_net,
                channel=channel,
                category_id=p.category_id,
                brand_id=p.brand_id,
                product_id=p.id,
                variant_id=v.id,
                customer_group_id=None,
                allow_additional_promotions=bool(best.allow_additional_promotions) if best else False,
                is_discounted_offer=is_discounted_offer,
                rules=rules,
                shop_id=shop_id,
                product_group_id=p.group_id,
                feature_value_ids=(fv_map.get(p.id) or set()) if need_fv else _MISSING,
                cat_tree=cat_tree,
            )
            out[v.id] = Decimal(sale_net).quantize(CENT)
    return out


def record_prices(*, shop_id: int, channel: str, now=None) -> dict[str, int]:
    """Uždaro pasikeitusių kainų intervalus ir atidaro naujus su 30 d. ankstesne kaina."""
    now = now or timezone.now()
    nets = compute_sale_nets(shop_id=shop_id, channel=channel)
    open_rows = {
        r.variant_id: r
        for r in VariantPriceHistory.objects.filter(shop_id=shop_id, channel=channel, valid_to__isnull=True)
    }
    changed = [vid for vid, net in nets.items() if vid not in open_rows or open_rows[vid].unit_net != net]
    close_ids = [open_rows[vid].id for vid in changed if vid in open_rows]
    close_ids += [r.id for vid, r in open_rows.items() if vid not in nets]
    with transaction.atomic():
        for i in range(0, len(close_ids), BATCH):
            VariantPriceHistory.objects.filter(id__in=close_ids[i:i + BATCH]).update(valid_to=now)
        priors: dict[int, Decimal] = {}
        for i in range(0, len(changed), BATCH):
            priors.update(
                VariantPriceHistory.objects.filter(
                    shop_id=shop_id,
                    channel=channel,
                    variant_id__in=changed[i:i + BATCH],
                    valid_to__gt=now - PRIOR_WINDOW,
                )
                .values("variant_id")
                .annotate(m=Min("unit_net"))
                .values_list("variant_id", "m")
            )
        VariantPriceHistory.objects.bulk_create(
            [
                VariantPriceHistory(
                    variant_id=vid,
                    shop_id=shop_id,
                    channel=channel,
                    unit_net=nets[vid],
                    prior_unit_net=priors.get(vid),
                    valid_from=now,
                )
                for vid in changed
            ],
            batch_size=BATCH,
        )
    return {"variants": len(nets), "changed": len(changed), "closed": len(close_ids)}


def record_all_prices(now=None) -> dict:
    from api.models import Shop

    stats = {}
    for shop in Shop.objects.filter(is_active=True).order_by("id"):
        for channel in CHANNELS:
            stats[f"{shop.code}/{channel}"] = record_prices(shop_id=shop.id, channel=channel, now=now)
    return stats


def variant_price_rows(variant_ids, *, shop_id: int | None, channel: str) -> dict[int, tuple[Decimal, Decimal | None]]:
    """{variant_id: (dabartinė kaina, 30 d. ankstesnė kaina)} iš atvirų intervalų."""
    if not shop_id or not variant_ids:
        return {}
    return {
        vid: (net, prior)
        for vid, net, prior in VariantPriceHistory.objects.filter(
            shop_id=shop_id, channel=channel, valid_to__isnull=True, variant_id__in=list(variant_ids)
        ).values_list("variant_id", "unit_net", "prior_unit_net")
    }


def product_prior_nets(product_ids, *, shop_id: int | None, channel: str) -> dict[int, Decimal]:
    """Prekės sąrašo ankstesnė kaina: min per aktyvius variantus, kur variantas be
    ankstesnės kainos atstovaujamas dabartine; jei kuris aktyvus variantas dar visai
    neįrašytas – prekei ankstesnės kainos nėra (nuolaidos nerodome)."""
    if not shop_id or not product_ids:
        return {}
    out: dict[int, Decimal] = {}
    seen: set[int] = set()
    for pid, vid, net, prior in VariantPriceHistory.objects.filter(
        shop_id=shop_id,
        channel=channel,
        valid_to__isnull=True,
        variant__product_id__in=list(product_ids),
        variant__is_active=True,
    ).values_list("variant__product_id", "variant_id", "unit_net", "prior_unit_net"):
        seen.add(vid)
        floor = prior if prior is not None else net
        out[pid] = min(out.get(pid, floor), floor)
    for pid, vid in Variant.objects.filter(
        product_id__in=list(product_ids), is_active=True
    ).values_list("product_id", "id"):
        if vid not in seen:
            out.pop(pid, None)
    return out


def omnibus_compare(
    *, list_net: Decimal, sale_net: Decimal, prior_net: Decimal | None
) -> tuple[Decimal | None, int | None]:
    """(perbraukta kaina be PVM, nuolaidos %). OMNIBUS_ENFORCE: nuo 30 d. mažiausios kainos."""
    if not getattr(settings, "OMNIBUS_ENFORCE", False):
        pct = discount_percent(list_unit_net=list_net, sale_unit_net=sale_net)
        return (Decimal(list_net) if pct is not None else None), pct
    if prior_net is None:
        return None, None
    pct = discount_percent(list_unit_net=prior_net, sale_unit_net=sale_net)
    return (Decimal(prior_net) if pct is not None else None), pct
