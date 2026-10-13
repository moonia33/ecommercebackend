from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from django.db import models
from django.utils import timezone

from .models import ShopPriceList, ShopPriceRule, Variant


@dataclass
class ShopPriceResult:
    unit_net: Decimal
    matched_rule_id: int | None = None


def _rule_matches_variant(rule: ShopPriceRule, variant: Variant) -> bool:
    p = variant.product
    if rule.variant_id and int(rule.variant_id) != int(variant.id):
        return False
    if rule.product_id and int(rule.product_id) != int(variant.product_id):
        return False
    if rule.category_id and int(rule.category_id) != int(getattr(p, "category_id", 0) or 0):
        return False
    if rule.brand_id and int(rule.brand_id) != int(getattr(p, "brand_id", 0) or 0):
        return False
    if rule.product_group_id and int(rule.product_group_id) != int(getattr(p, "group_id", 0) or 0):
        return False
    return True


def _apply_rule(base: Decimal, rule: ShopPriceRule) -> Decimal:
    out = Decimal(base)
    if rule.rule_type == ShopPriceRule.RuleType.FIXED_PRICE and rule.amount_eur is not None:
        out = Decimal(rule.amount_eur)
    elif rule.rule_type == ShopPriceRule.RuleType.PERCENT_DISCOUNT and rule.percent is not None:
        pct = max(Decimal("0"), min(Decimal("100"), Decimal(rule.percent)))
        out = (out * (Decimal("100") - pct) / Decimal("100")).quantize(Decimal("0.01"))
    elif rule.rule_type == ShopPriceRule.RuleType.DELTA and rule.amount_eur is not None:
        out = (out + Decimal(rule.amount_eur)).quantize(Decimal("0.01"))

    if rule.min_price_eur is not None:
        out = max(out, Decimal(rule.min_price_eur))
    return max(out, Decimal("0.00"))


def resolve_shop_variant_price(
    *,
    variant: Variant,
    shop_id: int | None,
    channel_type: str = "retail",
    customer_group_id: int | None = None,
) -> ShopPriceResult:
    base = Decimal(variant.price_eur)
    if not shop_id:
        return ShopPriceResult(unit_net=base, matched_rule_id=None)

    today: date = timezone.now().date()
    lists = (
        ShopPriceList.objects.filter(
            shop_id=shop_id,
            is_active=True,
            channel_type=channel_type,
        )
        .filter(models.Q(valid_from__isnull=True) | models.Q(valid_from__lte=today))
        .filter(models.Q(valid_to__isnull=True) | models.Q(valid_to__gte=today))
        .order_by("-priority", "id")
    )
    if customer_group_id:
        lists = lists.filter(models.Q(customer_group_id=customer_group_id) | models.Q(customer_group__isnull=True))
    else:
        lists = lists.filter(customer_group__isnull=True)

    for pl in lists:
        rules = pl.rules.filter(is_active=True).order_by("-priority", "id")
        for rule in rules:
            if not _rule_matches_variant(rule, variant):
                continue
            return ShopPriceResult(unit_net=_apply_rule(base, rule), matched_rule_id=int(rule.id))

    return ShopPriceResult(unit_net=base, matched_rule_id=None)
