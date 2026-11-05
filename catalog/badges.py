"""Badge taisyklių variklis ir badge'ų skaitymas API/indeksui.

apply_badge_rules(): kiekvienai aktyviai taisyklei suranda atitinkančias
prekes ir sinchronizuoja jos ProductBadge(source=rule) įrašus – prideda
naujus, nuima nebeatitinkančius. Neaktyvių taisyklių priskyrimai nuimami.
Rankiniai ir importo priskyrimai neliečiami.
"""

from __future__ import annotations

import logging
import re
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import timedelta

from django.db import transaction
from django.db.models import Exists, F, OuterRef, Q
from django.utils import timezone

from .enrichment import _normalize_text, _rule_scope_q
from .models import (
    Badge,
    BadgeRule,
    InventoryItem,
    Product,
    ProductBadge,
    ProductFeatureValue,
    VariantFeatureValue,
)

logger = logging.getLogger(__name__)


@dataclass
class BadgeRuleResult:
    matched: dict[int, int] = field(default_factory=dict)
    added: int = 0
    removed: int = 0
    changed_product_ids: set[int] = field(default_factory=set)


def _rule_queryset(rule: BadgeRule):
    qs = Product.objects.filter(_rule_scope_q(rule))
    if rule.sku_prefix:
        p = rule.sku_prefix.strip()
        qs = qs.filter(Q(sku__istartswith=p) | Q(variants__sku__istartswith=p))
    if rule.feature_id:
        pf = {"product_id": OuterRef("pk"), "feature_id": rule.feature_id}
        vf = {"variant__product_id": OuterRef("pk"), "feature_id": rule.feature_id}
        if rule.feature_value_id:
            pf["feature_value_id"] = vf["feature_value_id"] = rule.feature_value_id
        qs = qs.filter(
            Exists(ProductFeatureValue.objects.filter(**pf))
            | Exists(VariantFeatureValue.objects.filter(**vf))
        )
    if rule.in_stock_only or rule.discounted_only or rule.warehouse_id:
        inv = InventoryItem.objects.filter(
            variant__product_id=OuterRef("pk"), variant__is_active=True
        )
        if rule.warehouse_id:
            inv = inv.filter(warehouse_id=rule.warehouse_id)
        if rule.in_stock_only:
            inv = inv.filter(qty_on_hand__gt=F("qty_reserved"))
        if rule.discounted_only:
            inv = inv.filter(never_discount=False).filter(
                Q(offer_discount_percent__gt=0)
                | Q(offer_price_override_eur__lt=F("variant__price_eur"))
            )
        qs = qs.filter(Exists(inv))
    if rule.created_within_days:
        qs = qs.filter(
            created_at__gte=timezone.now() - timedelta(days=int(rule.created_within_days))
        )
    return qs.distinct()


def _text_matcher(rule: BadgeRule):
    pattern = (rule.pattern or "").strip()
    if not pattern:
        return None
    if rule.matcher_type == BadgeRule.MatcherType.REGEX:
        rx = re.compile(pattern, re.IGNORECASE)
        return lambda text: bool(rx.search(text))
    needle = _normalize_text(pattern)
    return lambda text: needle in text


def rule_product_ids(rule: BadgeRule) -> set[int]:
    qs = _rule_queryset(rule)
    matcher = _text_matcher(rule)
    if matcher is None:
        return set(qs.values_list("id", flat=True))
    out: set[int] = set()
    for p in qs.only("id", "name", "description", "sku").iterator():
        texts = []
        if rule.match_in_name:
            texts.append(_normalize_text(p.name or ""))
        if rule.match_in_description:
            texts.append(_normalize_text(p.description or ""))
        if rule.match_in_sku:
            texts.append(_normalize_text(p.sku or ""))
        if any(matcher(t) for t in texts if t):
            out.add(p.id)
    return out


def apply_badge_rules(
    *, rule_ids: Iterable[int] | None = None, dry_run: bool = False, reindex: bool = True
) -> BadgeRuleResult:
    result = BadgeRuleResult()
    rules = BadgeRule.objects.select_related("badge")
    if rule_ids is not None:
        rules = rules.filter(id__in=list(rule_ids))
    with transaction.atomic():
        for rule in rules:
            current = set(
                ProductBadge.objects.filter(rule=rule).values_list("product_id", flat=True)
            )
            wanted = rule_product_ids(rule) if rule.is_active else set()
            result.matched[rule.id] = len(wanted)
            add, remove = wanted - current, current - wanted
            result.added += len(add)
            result.removed += len(remove)
            result.changed_product_ids |= add | remove
            if dry_run:
                continue
            if remove:
                ProductBadge.objects.filter(rule=rule, product_id__in=remove).delete()
            ProductBadge.objects.bulk_create(
                [
                    ProductBadge(
                        product_id=pid, badge=rule.badge, rule=rule,
                        source=ProductBadge.Source.RULE,
                    )
                    for pid in add
                ],
                ignore_conflicts=True,
            )
        if dry_run:
            transaction.set_rollback(True)
    if reindex and not dry_run and result.changed_product_ids:
        reindex_products(result.changed_product_ids)
    return result


def sync_import_badges(product: Product, codes: list[str]) -> bool:
    """Importo badge'ai: lieka tik payload'e esantys. Nežinomas kodas sukuriamas."""
    wanted = {c.strip() for c in codes if c and c.strip()}
    existing = {
        pb.badge.code: pb
        for pb in ProductBadge.objects.filter(
            product=product, source=ProductBadge.Source.IMPORT, rule__isnull=True
        ).select_related("badge")
    }
    changed = False
    for code, pb in existing.items():
        if code not in wanted:
            pb.delete()
            changed = True
    for code in wanted - set(existing):
        badge, created = Badge.objects.get_or_create(code=code, defaults={"name": code})
        if created:
            logger.info("badge '%s' created by import; set its name in admin", code)
        ProductBadge.objects.create(
            product=product, badge=badge, source=ProductBadge.Source.IMPORT
        )
        changed = True
    return changed


def badges_for_products(
    product_ids: Iterable[int], *, language_code: str | None = None
) -> dict[int, list[dict]]:
    """{product_id: [{code, name, style, color}]} – tik galiojantys, be dublių."""
    ids = list(product_ids)
    if not ids:
        return {}
    today = timezone.localdate()
    links = (
        ProductBadge.objects.filter(product_id__in=ids)
        .filter(Badge.live_q("badge__", today))
        .filter(Q(valid_until__isnull=True) | Q(valid_until__gte=today))
        .select_related("badge")
        .prefetch_related("badge__translations")
        .order_by("-badge__priority", "badge__code")
    )
    out: dict[int, list[dict]] = defaultdict(list)
    seen: set[tuple[int, int]] = set()
    for link in links:
        key = (link.product_id, link.badge_id)
        if key in seen:
            continue
        seen.add(key)
        b = link.badge
        name = b.name
        if language_code:
            for tr in b.translations.all():
                if tr.language_code == language_code and tr.name:
                    name = tr.name
                    break
        out[link.product_id].append(
            {"code": b.code, "name": name, "style": b.style, "color": b.color}
        )
    return dict(out)


def live_badge_q(code: str) -> Q:
    today = timezone.localdate()
    return Q(
        Exists(
            ProductBadge.objects.filter(product_id=OuterRef("pk"), badge__code=code)
            .filter(Badge.live_q("badge__", today))
            .filter(Q(valid_until__isnull=True) | Q(valid_until__gte=today))
        )
    )


def reindex_products(product_ids: Iterable[int]) -> None:
    ids = sorted({int(i) for i in product_ids})
    if not ids:
        return

    def _dispatch(ids=ids):
        from catalog.signals import bump_listing_ver
        from config.celery import safe_delay
        from search.tasks import sync_products_task

        bump_listing_ver()
        for i in range(0, len(ids), 200):
            chunk = ids[i : i + 200]
            try:
                if safe_delay(sync_products_task, chunk) is not None:
                    continue
            except Exception:
                logger.exception("failed to enqueue badge reindex")
            try:
                from search.services import index_products

                index_products(chunk)
            except Exception:
                logger.exception("inline badge reindex failed")

    transaction.on_commit(_dispatch)
