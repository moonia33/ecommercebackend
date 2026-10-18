from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation

from catalog.models import FeatureValueGroup, OptionValueGroup

_RANGE_RE = re.compile(
    r"(\d+(?:[.,]\d+)?)\s*[-–—]\s*(\d+(?:[.,]\d+)?)"
)
_SINGLE_RE = re.compile(r"(\d+(?:[.,]\d+)?)")


def _num(s: str) -> Decimal:
    return Decimal(s.replace(",", "."))


def parse_numeric_range(text: str) -> tuple[Decimal, Decimal] | None:
    """Parse a numeric range or single number out of a raw value label.

    '10-30' -> (10, 30); '10–35 g' -> (10, 35); '30 g' -> (30, 30);
    '12cm/4.75in' -> None (ambiguous compound). Returns None when no
    clean numeric range can be derived.
    """
    t = (text or "").strip()
    if not t:
        return None
    t = t.replace(",", ".")
    m = _RANGE_RE.search(t)
    if m:
        lo, hi = _num(m.group(1)), _num(m.group(2))
        return (min(lo, hi), max(lo, hi))
    if "/" in t:
        return None
    m = _SINGLE_RE.search(t)
    if m:
        n = _num(m.group(1))
        return (n, n)
    return None


def _within(
    rng: tuple[Decimal, Decimal],
    min_value: Decimal | None,
    max_value: Decimal | None,
) -> bool:
    """True when the value's range fits inside the group's bounds.

    A rod rated '10-30' belongs to 'iki 35 g' (max <= 35) but '20-100'
    does not — it cannot throw a 5 g lure even though the ranges overlap.
    """
    lo, hi = rng
    if min_value is not None and lo < min_value:
        return False
    if max_value is not None and hi > max_value:
        return False
    return True


def auto_assign_feature_group(group: FeatureValueGroup) -> int:
    """Assign feature values whose parsed range fits the group bounds.

    Returns the number of newly added members. When the group has no
    numeric bounds, no auto-assignment is possible -> 0.
    """
    if group.min_value is None and group.max_value is None:
        return 0
    added = 0
    for v in group.feature.values.filter(is_active=True).prefetch_related("groups"):
        if v.groups.filter(pk=group.pk).exists():
            continue
        rng = parse_numeric_range(v.value)
        if rng is None:
            continue
        if _within(rng, group.min_value, group.max_value):
            group.values.add(v)
            added += 1
    return added


def auto_assign_option_group(group: OptionValueGroup) -> int:
    """Same as auto_assign_feature_group but for option values."""
    if group.min_value is None and group.max_value is None:
        return 0
    added = 0
    for v in group.option_type.values.filter(is_active=True).prefetch_related("groups"):
        if v.groups.filter(pk=group.pk).exists():
            continue
        rng = parse_numeric_range(v.label) or parse_numeric_range(v.code)
        if rng is None:
            continue
        if _within(rng, group.min_value, group.max_value):
            group.values.add(v)
            added += 1
    return added
