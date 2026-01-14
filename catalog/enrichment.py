from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from .models import (
    Category,
    EnrichmentMatch,
    EnrichmentRule,
    EnrichmentRun,
    FeatureValue,
    Product,
    ProductFeatureValue,
)


User = get_user_model()


@dataclass(frozen=True)
class EnrichmentResult:
    processed_products: int
    matched: int
    assigned: int
    created_feature_values: int
    skipped_existing: int
    skipped_conflict: int


def _normalize_text(value: str) -> str:
    s = (value or "").strip().lower()
    if not s:
        return ""
    s = s.replace("\u2033", '"')
    s = s.replace("\u00d7", "x")
    s = unicodedata.normalize("NFKC", s)
    s = re.sub(r"\s+", " ", s)
    return s


def _format_value(value: str, fmt: str) -> str:
    v = (value or "").strip()
    if not v:
        return ""
    if fmt == EnrichmentRule.ValueFormat.DECIMAL_TRIM:
        vv = v.replace(",", ".")
        try:
            d = Decimal(vv)
        except Exception:
            return v
        if d == d.to_integral():
            return str(int(d))
        s = format(d.normalize(), "f")
        if "." in s:
            s = s.rstrip("0").rstrip(".")
        return s
    return v


def _descendant_category_ids(*, root_id: int) -> list[int]:
    rows = Category.objects.filter(is_active=True).values("id", "parent_id")
    children: dict[int, list[int]] = {}
    for r in rows:
        pid = r["parent_id"]
        if pid is None:
            continue
        children.setdefault(int(pid), []).append(int(r["id"]))

    out: list[int] = []
    stack = [int(root_id)]
    seen: set[int] = set()
    while stack:
        cid = stack.pop()
        if cid in seen:
            continue
        seen.add(cid)
        out.append(cid)
        stack.extend(children.get(cid, []))
    return out


def _rule_scope_q(rule: EnrichmentRule) -> Q:
    q = Q(is_active=True)

    if rule.brand_id:
        q &= Q(brand_id=rule.brand_id)

    if rule.product_group_id:
        q &= Q(group_id=rule.product_group_id)

    if rule.category_id:
        if rule.include_descendants:
            ids = _descendant_category_ids(root_id=int(rule.category_id))
            q &= Q(category_id__in=ids)
        else:
            q &= Q(category_id=rule.category_id)

    return q


def _collect_text_for_rule(product: Product, rule: EnrichmentRule) -> dict[str, str]:
    out: dict[str, str] = {}
    if rule.match_in_name:
        out[EnrichmentRule.MatchField.NAME] = _normalize_text(product.name or "")
    if rule.match_in_description:
        out[EnrichmentRule.MatchField.DESCRIPTION] = _normalize_text(product.description or "")
    if rule.match_in_sku:
        out[EnrichmentRule.MatchField.SKU] = _normalize_text(product.sku or "")
    return out


def _apply_rule_to_product(*, product: Product, rule: EnrichmentRule, dry_run: bool, run: EnrichmentRun) -> tuple[bool, bool, bool, int, int, int]:
    texts = _collect_text_for_rule(product, rule)
    if not texts:
        return (False, False, False, 0, 0, 0)

    matched_field = ""
    matched_text = ""
    extracted = ""

    if rule.matcher_type == EnrichmentRule.MatcherType.CONTAINS:
        needle = _normalize_text(rule.pattern)
        if not needle:
            return (False, False, False, 0, 0, 0)
        for field, text in texts.items():
            if needle and needle in text:
                matched_field = field
                matched_text = needle
                extracted = rule.value_template or rule.fixed_value or needle
                break

    elif rule.matcher_type == EnrichmentRule.MatcherType.REGEX:
        pat = (rule.pattern or "").strip()
        if not pat:
            return (False, False, False, 0, 0, 0)
        try:
            rx = re.compile(pat, flags=re.IGNORECASE)
        except re.error:
            return (False, False, False, 0, 0, 0)

        for field, text in texts.items():
            if not text:
                continue
            m = rx.search(text)
            if not m:
                continue
            matched_field = field
            matched_text = m.group(0) or ""
            grp = int(rule.extract_group or 0)
            if grp > 0 and grp <= (m.lastindex or 0):
                extracted = m.group(grp) or ""
            elif grp == 0:
                extracted = m.group(0) or ""
            else:
                extracted = m.group(0) or ""
            break

    if not matched_field:
        return (False, False, False, 0, 0, 0)

    extracted = _format_value(extracted, rule.value_format)
    if rule.value_template and rule.matcher_type == EnrichmentRule.MatcherType.REGEX:
        tmpl = rule.value_template
        extracted = tmpl.replace("{{value}}", extracted)

    extracted = (extracted or "").strip()
    if not extracted:
        return (True, False, False, 0, 0, 0)

    if not rule.feature_id:
        return (True, False, False, 0, 0, 0)

    feature = rule.feature

    conflict = False
    existing_for_feature = ProductFeatureValue.objects.filter(product=product, feature=feature).exists()
    if existing_for_feature and not feature.allows_multiple:
        conflict = True

    created_feature_value = 0
    assigned = False
    skipped_existing = 0
    skipped_conflict = 0

    if conflict:
        EnrichmentMatch.objects.create(
            run=run,
            rule=rule,
            product=product,
            matched_field=matched_field,
            matched_text=matched_text,
            extracted_value=extracted,
            action=EnrichmentMatch.Action.SKIPPED_CONFLICT,
        )
        return (True, True, False, 0, 0, 1)

    fv = FeatureValue.objects.filter(feature=feature, value=extracted).first()
    if fv is None:
        created_feature_value = 1

    if dry_run:
        if fv is None:
            assigned = True
        else:
            exists = ProductFeatureValue.objects.filter(product=product, feature_value=fv).exists()
            if exists:
                skipped_existing = 1
            else:
                assigned = True
        EnrichmentMatch.objects.create(
            run=run,
            rule=rule,
            product=product,
            matched_field=matched_field,
            matched_text=matched_text,
            extracted_value=extracted,
            action=EnrichmentMatch.Action.ASSIGNED if assigned else EnrichmentMatch.Action.SKIPPED_EXISTS,
        )
        return (True, True, assigned, created_feature_value, skipped_existing, skipped_conflict)

    if fv is None:
        fv = FeatureValue.objects.create(feature=feature, value=extracted, is_active=True)

    pfv, created = ProductFeatureValue.objects.get_or_create(
        product=product,
        feature=feature,
        feature_value=fv,
    )
    if created:
        assigned = True
    else:
        skipped_existing = 1

    EnrichmentMatch.objects.create(
        run=run,
        rule=rule,
        product=product,
        matched_field=matched_field,
        matched_text=matched_text,
        extracted_value=extracted,
        action=EnrichmentMatch.Action.ASSIGNED if assigned else EnrichmentMatch.Action.SKIPPED_EXISTS,
    )

    return (True, True, assigned, created_feature_value, skipped_existing, skipped_conflict)


def apply_enrichment_rules(
    *,
    dry_run: bool,
    rule_ids: list[int] | None = None,
    since: datetime | None = None,
    limit: int | None = None,
    triggered_by: User | None = None,
) -> tuple[EnrichmentRun, EnrichmentResult]:
    qs_rules = EnrichmentRule.objects.filter(is_active=True)
    if rule_ids:
        qs_rules = qs_rules.filter(id__in=[int(i) for i in rule_ids])
    qs_rules = qs_rules.select_related("feature", "brand", "category", "product_group").order_by("-priority", "id")
    rules = list(qs_rules)

    run = EnrichmentRun.objects.create(
        status=EnrichmentRun.Status.RUNNING,
        dry_run=bool(dry_run),
        triggered_by=triggered_by,
        started_at=timezone.now(),
    )

    processed_products = 0
    matched = 0
    assigned = 0
    created_feature_values = 0
    skipped_existing = 0
    skipped_conflict = 0

    try:
        with transaction.atomic():
            products_qs = Product.objects.filter(is_active=True)
            if since is not None:
                products_qs = products_qs.filter(updated_at__gte=since)
            products_qs = products_qs.select_related("brand", "category").order_by("id")

            if limit is not None:
                products_qs = products_qs[: max(0, int(limit))]

            for product in products_qs.iterator(chunk_size=200):
                processed_products += 1
                for rule in rules:
                    if not rule.feature_id:
                        continue

                    if not Product.objects.filter(id=product.id).filter(_rule_scope_q(rule)).exists():
                        continue

                    did_process, did_match, did_assign, created_fv, skipped_ex, skipped_cf = _apply_rule_to_product(
                        product=product,
                        rule=rule,
                        dry_run=dry_run,
                        run=run,
                    )
                    if not did_process:
                        continue
                    if did_match:
                        matched += 1
                    if did_assign:
                        assigned += 1
                    created_feature_values += created_fv
                    skipped_existing += skipped_ex
                    skipped_conflict += skipped_cf

                if limit is not None and processed_products >= int(limit):
                    break

        run.status = EnrichmentRun.Status.DONE
        run.finished_at = timezone.now()
        run.summary = {
            "processed_products": processed_products,
            "matched": matched,
            "assigned": assigned,
            "created_feature_values": created_feature_values,
            "skipped_existing": skipped_existing,
            "skipped_conflict": skipped_conflict,
        }
        run.save(update_fields=["status", "finished_at", "summary"])

        return (
            run,
            EnrichmentResult(
                processed_products=processed_products,
                matched=matched,
                assigned=assigned,
                created_feature_values=created_feature_values,
                skipped_existing=skipped_existing,
                skipped_conflict=skipped_conflict,
            ),
        )
    except Exception as exc:
        run.status = EnrichmentRun.Status.FAILED
        run.finished_at = timezone.now()
        run.error = str(exc)
        run.save(update_fields=["status", "finished_at", "error"])
        raise
