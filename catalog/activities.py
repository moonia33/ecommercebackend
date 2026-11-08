"""Veiklų taisyklių variklis ir skaitymas API/indeksui (žr. docs/activities.md).

apply_activity_rules(): kiekvienai taisyklei sinchronizuoja ProductActivity
(source=rule) – prideda naujus, nuima nebeatitinkančius. Rankiniai
priskyrimai ir išėmimai neliečiami; išėmimas (source=exclude) nugali
taisykles skaitymo metu (indeksas, API).
"""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field

from django.db import transaction
from django.db.models import Exists, OuterRef, Q

from .badges import reindex_products
from .enrichment import _descendant_category_ids, _normalize_text
from .models import (
    Activity,
    ActivityRule,
    Product,
    ProductActivity,
    ProductFeatureValue,
    VariantFeatureValue,
)

POSITIVE = ProductActivity.Source.RULE, ProductActivity.Source.MANUAL


@dataclass
class ActivityRuleResult:
    matched: dict[int, int] = field(default_factory=dict)
    added: int = 0
    removed: int = 0
    changed_product_ids: set[int] = field(default_factory=set)


def _category_ids(categories, include_descendants: bool) -> set[int]:
    out: set[int] = set()
    for cid in categories:
        out.add(int(cid))
        if include_descendants:
            out.update(_descendant_category_ids(root_id=int(cid)))
    return out


def _rule_queryset(rule: ActivityRule):
    cat_ids = list(rule.categories.values_list("id", flat=True))
    brand_ids = list(rule.brands.values_list("id", flat=True))
    pattern = (rule.pattern or "").strip()
    if not (cat_ids or brand_ids or rule.product_group_id or rule.sku_prefix or rule.feature_id or pattern):
        return None
    qs = Product.objects.filter(is_active=True)
    if cat_ids:
        qs = qs.filter(category_id__in=_category_ids(cat_ids, rule.include_descendants))
    excl = list(rule.exclude_categories.values_list("id", flat=True))
    if excl:
        qs = qs.exclude(category_id__in=_category_ids(excl, True))
    if brand_ids:
        qs = qs.filter(brand_id__in=brand_ids)
    if rule.product_group_id:
        qs = qs.filter(group_id=rule.product_group_id)
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
    return qs.distinct()


def _text_matcher(rule: ActivityRule):
    pattern = (rule.pattern or "").strip()
    if not pattern:
        return None
    if rule.matcher_type == ActivityRule.MatcherType.REGEX:
        rx = re.compile(pattern, re.IGNORECASE)
        return lambda text: bool(rx.search(text))
    needles = [_normalize_text(n) for n in pattern.split("|") if n.strip()]
    return lambda text: any(n in text for n in needles)


def rule_product_ids(rule: ActivityRule) -> set[int]:
    qs = _rule_queryset(rule)
    if qs is None:
        return set()
    matcher = _text_matcher(rule)
    if matcher is None:
        return set(qs.values_list("id", flat=True))
    out: set[int] = set()
    for p in qs.only("id", "name", "description").iterator():
        texts = []
        if rule.match_in_name:
            texts.append(_normalize_text(p.name or ""))
        if rule.match_in_description:
            texts.append(_normalize_text(p.description or ""))
        if any(matcher(t) for t in texts if t):
            out.add(p.id)
    return out


def apply_activity_rules(
    *, rule_ids: Iterable[int] | None = None, dry_run: bool = False, reindex: bool = True
) -> ActivityRuleResult:
    result = ActivityRuleResult()
    rules = ActivityRule.objects.select_related("activity")
    if rule_ids is not None:
        rules = rules.filter(id__in=list(rule_ids))
    with transaction.atomic():
        for rule in rules:
            stale = ProductActivity.objects.filter(rule=rule).exclude(activity_id=rule.activity_id)
            stale_ids = set(stale.values_list("product_id", flat=True))
            current = set(
                ProductActivity.objects.filter(rule=rule, activity_id=rule.activity_id)
                .values_list("product_id", flat=True)
            )
            wanted = rule_product_ids(rule) if rule.is_active and rule.activity.is_active else set()
            result.matched[rule.id] = len(wanted)
            add, remove = wanted - current, current - wanted
            result.added += len(add)
            result.removed += len(remove) + len(stale_ids)
            result.changed_product_ids |= add | remove | stale_ids
            if dry_run:
                continue
            stale.delete()
            if remove:
                ProductActivity.objects.filter(rule=rule, product_id__in=remove).delete()
            ProductActivity.objects.bulk_create(
                [
                    ProductActivity(
                        product_id=pid, activity=rule.activity, rule=rule,
                        source=ProductActivity.Source.RULE,
                    )
                    for pid in add
                ],
                ignore_conflicts=True,
                batch_size=2000,
            )
        if dry_run:
            transaction.set_rollback(True)
    if not dry_run and result.changed_product_ids:
        from .signals import _bump_catalog_facets_cache, bump_listing_ver

        bump_listing_ver()
        _bump_catalog_facets_cache()
        if reindex:
            reindex_products(result.changed_product_ids)
    return result


def activity_codes_from_links(links) -> list[str]:
    """Iš prefetch'intų ProductActivity (su activity__parent) – veiklos + jų sritys."""
    links = list(links)
    excluded = {l.activity_id for l in links if l.source == ProductActivity.Source.EXCLUDE}
    codes: set[str] = set()
    for l in links:
        a = l.activity
        if l.source == ProductActivity.Source.EXCLUDE or not a.is_active:
            continue
        if a.id in excluded or (a.parent_id and a.parent_id in excluded):
            continue
        codes.add(a.code)
        if a.parent_id and a.parent.is_active:
            codes.add(a.parent.code)
    return sorted(codes)


def activity_subtree_ids(activity: Activity) -> list[int]:
    ids = [activity.id]
    if not activity.parent_id:
        ids += list(activity.children.filter(is_active=True).values_list("id", flat=True))
    return ids


def activity_q(activity: Activity) -> Q:
    """ORM filtras: prekė priklauso veiklai (arba bet kuriai srities veiklai)."""
    ids = activity_subtree_ids(activity)
    positive = ProductActivity.objects.filter(
        product_id=OuterRef("pk"), activity_id__in=ids, source__in=POSITIVE
    ).exclude(
        Exists(
            ProductActivity.objects.filter(
                product_id=OuterRef("product_id"),
                source=ProductActivity.Source.EXCLUDE,
            ).filter(
                Q(activity_id=OuterRef("activity_id"))
                | Q(activity_id=OuterRef("activity__parent_id"))
            )
        )
    )
    return Q(Exists(positive))


def activity_product_counts(product_qs=None) -> dict[int, int]:
    """{activity_id: aktyvių prekių skaičius}; srities skaičius – unikalios prekės per visas veiklas."""
    qs = ProductActivity.objects.filter(source__in=POSITIVE, product__is_active=True)
    if product_qs is not None:
        qs = qs.filter(product_id__in=product_qs.values("id"))
    excluded = set(
        ProductActivity.objects.filter(source=ProductActivity.Source.EXCLUDE).values_list(
            "product_id", "activity_id"
        )
    )
    per: dict[int, set[int]] = defaultdict(set)
    meta = {aid: (parent, active) for aid, parent, active in
            Activity.objects.values_list("id", "parent_id", "is_active")}
    for pid, aid in qs.values_list("product_id", "activity_id").distinct().iterator():
        parent, active = meta.get(aid, (None, False))
        if not active or (pid, aid) in excluded:
            continue
        if parent and (not meta.get(parent, (None, False))[1] or (pid, parent) in excluded):
            continue
        per[aid].add(pid)
        if parent:
            per[parent].add(pid)
    return {aid: len(pids) for aid, pids in per.items()}


def localized(activity: Activity, language_code: str | None) -> dict:
    out = {
        "name": activity.name,
        "short_description": activity.short_description,
        "content_json": activity.content_json,
        "seo_title": activity.seo_title,
        "seo_description": activity.seo_description,
    }
    if language_code:
        for tr in activity.translations.all():
            if tr.language_code == language_code:
                for k in out:
                    v = getattr(tr, k)
                    if v:
                        out[k] = v
                break
    return out
