from __future__ import annotations

from django.db.models import Q, QuerySet

from .models import Product, ShopAssortmentRule
from .services.catalog_core import descendant_category_ids


def _rule_q(rule: ShopAssortmentRule) -> Q:
    q = Q()
    has_any = False
    if rule.product_id:
        q &= Q(id=rule.product_id)
        has_any = True
    if rule.category_id:
        ids = descendant_category_ids(root_id=int(rule.category_id))
        q &= Q(category_id__in=ids)
        has_any = True
    if rule.brand_id:
        q &= Q(brand_id=rule.brand_id)
        has_any = True
    if rule.product_group_id:
        q &= Q(group_id=rule.product_group_id)
        has_any = True
    # Empty rule should never hide everything.
    return q if has_any else Q(pk__in=[])


def apply_shop_product_scope(*, qs: QuerySet[Product], shop_id: int | None) -> QuerySet[Product]:
    if not shop_id:
        return qs

    rules = list(
        ShopAssortmentRule.objects.filter(shop_id=shop_id, is_active=True).order_by("-priority", "id")
    )
    if not rules:
        return qs

    include_q = Q(pk__in=[])
    exclude_q = Q(pk__in=[])
    has_include = False
    for r in rules:
        rq = _rule_q(r)
        if r.scope == ShopAssortmentRule.Scope.INCLUDE:
            include_q |= rq
            has_include = True
        else:
            exclude_q |= rq

    scoped = qs
    if has_include:
        scoped = scoped.filter(include_q)
    scoped = scoped.exclude(exclude_q)
    return scoped
