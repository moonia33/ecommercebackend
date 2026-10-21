from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation

from ninja.errors import HttpError

from catalog.models import Category


def parse_pairs(value: str | None) -> list[tuple[str, str]]:
    if not value:
        return []
    out: list[tuple[str, str]] = []
    for part in [p.strip() for p in value.split(",") if p.strip()]:
        if ":" not in part:
            raise HttpError(400, "Invalid pair format; expected code:value")
        k, v = part.split(":", 1)
        k = k.strip()
        v = v.strip()
        if not k or not v:
            raise HttpError(400, "Invalid pair format; expected code:value")
        out.append((k, v))
    return out


_RANGE_SEP_RE = re.compile(r"[;,]")


def parse_range_pairs(
    value: str | None,
) -> list[tuple[str, Decimal | None, Decimal | None]]:
    """Parse `range=` params: 'code:min-max[,;code2:min2-max2]'.

    Either bound may be empty (open-ended): 'w:10-' = at least 10,
    'w:-50' = up to 50. Negative bounds are not supported — a leading
    '-' means an empty lower bound.
    """
    if not value:
        return []
    out: list[tuple[str, Decimal | None, Decimal | None]] = []
    for part in _RANGE_SEP_RE.split(value):
        part = part.strip()
        if not part:
            continue
        if ":" not in part:
            raise HttpError(400, "Invalid range format; expected code:min-max")
        code, _, bounds = part.partition(":")
        code = code.strip()
        lo_s, _, hi_s = bounds.partition("-")
        lo_s, hi_s = lo_s.strip(), hi_s.strip()
        try:
            lo = Decimal(lo_s) if lo_s else None
            hi = Decimal(hi_s) if hi_s else None
        except InvalidOperation:
            raise HttpError(400, "Invalid range bounds; expected code:min-max")
        if not code or (lo is None and hi is None):
            raise HttpError(400, "Invalid range format; expected code:min-max")
        if lo is not None and hi is not None and lo > hi:
            lo, hi = hi, lo
        out.append((code, lo, hi))
    return out


def descendant_category_ids(*, root_id: int) -> list[int]:
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
