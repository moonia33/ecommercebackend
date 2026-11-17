from __future__ import annotations

import time as time_module
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from django.core.cache import cache
from django.db import transaction
from django.db.models import Case, IntegerField, Q, Value, When
from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver
from django.utils import timezone

from .models import DeliveryRule, Holiday


@dataclass(frozen=True)
class DeliveryWindow:
    min_date: date
    max_date: date
    kind: str = "estimated"
    rule_code: str = ""
    source: str = ""
    milestones: dict[str, Any] | None = None


def _is_weekend(d: date) -> bool:
    return d.weekday() >= 5


HOLIDAYS_CACHE_TTL = 3600
_HOLIDAYS_VERSION_KEY = "shipping:holidays:version"


def _holidays_by_country() -> dict[str, frozenset[date]]:
    # Versioned key: a change bumps the version after commit, so a reader that
    # queried before the change can only fill the old, no longer used key.
    version = cache.get_or_set(_HOLIDAYS_VERSION_KEY, time_module.time_ns, None)
    key = f"shipping:holidays:v{version}"
    data = cache.get(key)
    if data is None:
        grouped: dict[str, set[date]] = {}
        for cc, d in Holiday.objects.filter(is_active=True).values_list("country_code", "date"):
            grouped.setdefault((cc or "").upper(), set()).add(d)
        data = {cc: frozenset(ds) for cc, ds in grouped.items()}
        cache.set(key, data, HOLIDAYS_CACHE_TTL)
    return data


def _holiday_dates(country_code: str) -> frozenset[date]:
    return _holidays_by_country().get((country_code or "").upper(), frozenset())


def _bump_holidays_version() -> None:
    try:
        cache.incr(_HOLIDAYS_VERSION_KEY)
    except ValueError:
        cache.set(_HOLIDAYS_VERSION_KEY, time_module.time_ns(), None)


@receiver([post_save, post_delete], sender=Holiday)
def _invalidate_holidays_cache(sender, instance: Holiday, **kwargs) -> None:
    transaction.on_commit(_bump_holidays_version)


def is_business_day(*, d: date, country_code: str) -> bool:
    if _is_weekend(d):
        return False
    return d not in _holiday_dates(country_code)


def normalize_to_business_day(*, d: date, country_code: str) -> date:
    cur = d
    while not is_business_day(d=cur, country_code=country_code):
        cur = cur + timedelta(days=1)
    return cur


def add_business_days(*, start: date, days: int, country_code: str) -> date:
    cur = normalize_to_business_day(d=start, country_code=country_code)
    remaining = int(days)
    while remaining > 0:
        cur = cur + timedelta(days=1)
        if is_business_day(d=cur, country_code=country_code):
            remaining -= 1
    return cur


def _next_weekday_time(*, now: datetime, weekday: int, at_time: time) -> datetime:
    # weekday: Monday=0..Sunday=6
    candidate_date = now.date() + timedelta(days=(weekday - now.weekday()) % 7)
    candidate = datetime.combine(candidate_date, at_time, tzinfo=now.tzinfo)
    if candidate < now:
        candidate = candidate + timedelta(days=7)
    return candidate


def _select_delivery_rule(
    *,
    channel: str,
    warehouse_id: int | None,
    product_id: int | None,
    brand_id: int | None,
    category_id: int | None,
    product_group_id: int | None,
    today: date,
) -> DeliveryRule | None:
    qs = DeliveryRule.objects.filter(is_active=True)

    # Channel match: empty means "any"
    if channel:
        qs = qs.filter(Q(channel="") | Q(channel=channel))

    # Validity window
    qs = qs.filter(Q(valid_from__isnull=True) | Q(valid_from__lte=today))
    qs = qs.filter(Q(valid_to__isnull=True) | Q(valid_to__gte=today))

    # Targeting: rule may specify any subset; if specified, it must match
    if warehouse_id is not None:
        qs = qs.filter(Q(warehouse__isnull=True) | Q(warehouse_id=warehouse_id))
    if product_id is not None:
        qs = qs.filter(Q(product__isnull=True) | Q(product_id=product_id))
    if brand_id is not None:
        qs = qs.filter(Q(brand__isnull=True) | Q(brand_id=brand_id))
    if category_id is not None:
        qs = qs.filter(Q(category__isnull=True) | Q(category_id=category_id))
    if product_group_id is not None:
        qs = qs.filter(Q(product_group__isnull=True) | Q(product_group_id=product_group_id))

    # Prefer more specific rules when priority ties
    specificity = (
        Case(When(product__isnull=False, then=Value(8)), default=Value(0), output_field=IntegerField())
        + Case(When(category__isnull=False, then=Value(4)), default=Value(0), output_field=IntegerField())
        + Case(When(brand__isnull=False, then=Value(2)), default=Value(0), output_field=IntegerField())
        + Case(When(warehouse__isnull=False, then=Value(1)), default=Value(0), output_field=IntegerField())
    )

    # Prefer cycle-based rules when available; otherwise fall back to lead-time.
    # Only treat a CYCLE rule as preferred if it has required fields set.
    kind_preference = Case(
        When(
            kind=DeliveryRule.Kind.CYCLE,
            order_window_end_weekday__isnull=False,
            order_window_end_time__isnull=False,
            then=Value(1),
        ),
        default=Value(0),
        output_field=IntegerField(),
    )

    return (
        qs.annotate(_spec=specificity, _kind_pref=kind_preference)
        .order_by("-_kind_pref", "-priority", "-_spec", "code")
        .first()
    )


def estimate_delivery_window(
    *,
    now: datetime | None = None,
    country_code: str = "LT",
    channel: str = "normal",
    warehouse_id: int | None = None,
    product_id: int | None = None,
    brand_id: int | None = None,
    category_id: int | None = None,
    product_group_id: int | None = None,
) -> DeliveryWindow | None:
    now_dt = now or timezone.now()
    country_code = (country_code or "LT").strip().upper()
    channel = (channel or "normal").strip().lower()

    rule = _select_delivery_rule(
        channel=channel,
        warehouse_id=warehouse_id,
        product_id=product_id,
        brand_id=brand_id,
        category_id=category_id,
        product_group_id=product_group_id,
        today=now_dt.date(),
    )

    if not rule:
        return None

    milestones: dict[str, Any] = {}

    if rule.kind == DeliveryRule.Kind.CYCLE:
        if (
            rule.order_window_end_weekday is None
            or rule.order_window_end_time is None
        ):
            return None

        try:
            # Cycle-based rules are timezone-sensitive; interpret weekday/time in rule.timezone.
            try:
                rule_tz = ZoneInfo(str(rule.timezone or "Europe/Vilnius"))
            except Exception:
                rule_tz = now_dt.tzinfo

            now_local = now_dt.astimezone(rule_tz) if rule_tz else now_dt
            cycle_end_at = _next_weekday_time(
                now=now_local,
                weekday=int(rule.order_window_end_weekday),
                at_time=rule.order_window_end_time,
            )
        except Exception:
            return None

        milestones["cycle_end_at"] = cycle_end_at.isoformat()

        inbound_min = add_business_days(
            start=cycle_end_at.date(),
            days=int(rule.supplier_inbound_business_days_min or 0),
            country_code=country_code,
        )
        inbound_max = add_business_days(
            start=cycle_end_at.date(),
            days=int(rule.supplier_inbound_business_days_max or 0),
            country_code=country_code,
        )
        milestones["inbound_arrival_min_date"] = inbound_min.isoformat()

        ship_out_min = add_business_days(
            start=inbound_min,
            days=int(rule.warehouse_pack_business_days_min or 0),
            country_code=country_code,
        )
        ship_out_max = add_business_days(
            start=inbound_max,
            days=int(rule.warehouse_pack_business_days_max or 0),
            country_code=country_code,
        )
        milestones["ship_out_min_date"] = ship_out_min.isoformat()

        min_date = add_business_days(
            start=ship_out_min,
            days=int(rule.carrier_business_days_min or 0),
            country_code=country_code,
        )
        max_date = add_business_days(
            start=ship_out_max,
            days=int(rule.carrier_business_days_max or 0),
            country_code=country_code,
        )

        return DeliveryWindow(
            min_date=min_date,
            max_date=max_date,
            kind="estimated",
            rule_code=rule.code,
            source=(f"warehouse:{rule.warehouse.code}" if rule.warehouse_id else "rule"),
            milestones=milestones,
        )

    # Default: LEAD_TIME
    start_day = now_dt.date()
    if rule.cutoff_time is not None:
        cutoff_dt = datetime.combine(now_dt.date(), rule.cutoff_time, tzinfo=now_dt.tzinfo)
        if now_dt > cutoff_dt:
            start_day = start_day + timedelta(days=1)

    processing_min_end = add_business_days(
        start=start_day,
        days=int(rule.processing_business_days_min or 0),
        country_code=country_code,
    )
    processing_max_end = add_business_days(
        start=start_day,
        days=int(rule.processing_business_days_max or 0),
        country_code=country_code,
    )

    min_date = add_business_days(
        start=processing_min_end,
        days=int(rule.shipping_business_days_min or 0),
        country_code=country_code,
    )
    max_date = add_business_days(
        start=processing_max_end,
        days=int(rule.shipping_business_days_max or 0),
        country_code=country_code,
    )

    return DeliveryWindow(
        min_date=min_date,
        max_date=max_date,
        kind="estimated",
        rule_code=rule.code,
        source=(f"warehouse:{rule.warehouse.code}" if rule.warehouse_id else "rule"),
        milestones=None,
    )
