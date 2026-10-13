from __future__ import annotations

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
