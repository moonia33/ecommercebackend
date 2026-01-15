from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from django.conf import settings
from django.core.cache import cache

from .models import Category, ContentBlock, ContentBlockTranslation, ContentRule


@dataclass(frozen=True)
class ContentBlockResolved:
    key: str
    title: str
    placement: str
    type: str
    payload: dict


def _is_date_in_range(*, now: date, valid_from: date | None, valid_to: date | None) -> bool:
    if valid_from and now < valid_from:
        return False
    if valid_to and now > valid_to:
        return False
    return True


def _category_ancestor_ids(category_id: int | None) -> set[int]:
    if not category_id:
        return set()

    ids: set[int] = set()
    cur = Category.objects.filter(id=category_id).only("id", "parent_id").first()
    while cur is not None and cur.id not in ids:
        ids.add(int(cur.id))
        if not cur.parent_id:
            break
        cur = Category.objects.filter(id=cur.parent_id).only("id", "parent_id").first()
    return ids


def _translation_fallback_chain(language_code: str | None) -> list[str]:
    chain: list[str] = []
    if language_code:
        chain.append(language_code)
    default = (getattr(settings, "LANGUAGE_CODE", "") or "").split("-")[0]
    if default:
        chain.append(default)
    # Hardcoded safe fallbacks for this project
    chain.extend(["lt", "en"])

    # Keep order, unique
    seen: set[str] = set()
    out: list[str] = []
    for c in chain:
        c = (c or "").strip().lower()
        if not c or c in seen:
            continue
        out.append(c)
        seen.add(c)
    return out


def get_content_blocks_for_product(
    *,
    site_id: int | None = None,
    product_id: int,
    placement: str,
    channel: str,
    brand_id: int | None,
    category_id: int | None,
    product_group_id: int | None,
    language_code: str | None = None,
    now: date | None = None,
    cache_seconds: int = 120,
) -> list[ContentBlockResolved]:
    now_date = now or date.today()

    site_id_v = int(site_id) if site_id is not None else 0

    cache_key = (
        f"content_blocks:v1:site:{site_id_v}:product:{product_id}:pl:{placement}:ch:{channel}:"
        f"b:{brand_id or 0}:c:{category_id or 0}:g:{product_group_id or 0}:"
        f"lang:{(language_code or '').lower()}:d:{now_date.isoformat()}"
    )
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    allowed_channels = {"normal", "outlet"}
    channel = (channel or "normal").strip().lower()
    if channel not in allowed_channels:
        channel = "normal"

    placement = (placement or "product_detail").strip().lower()

    # 1) Active blocks for placement + global (global always included)
    block_qs = ContentBlock.objects.filter(
        is_active=True,
        placement__in=[placement, ContentBlock.Placement.GLOBAL],
    )
    if site_id_v:
        block_qs = block_qs.filter(site_id=site_id_v)

    blocks = list(
        block_qs.only(
            "id",
            "key",
            "type",
            "placement",
            "priority",
            "valid_from",
            "valid_to",
        )
    )
    blocks = [
        b
        for b in blocks
        if _is_date_in_range(now=now_date, valid_from=b.valid_from, valid_to=b.valid_to)
    ]
    blocks_by_id = {int(b.id): b for b in blocks}

    # 2) Active rules for those blocks
    rules = list(
        ContentRule.objects.filter(
            is_active=True,
            content_block_id__in=list(blocks_by_id.keys()),
        ).select_related("content_block", "category", "brand")
    )

    category_ancestors = _category_ancestor_ids(category_id)

    matched: list[tuple[ContentRule, ContentBlock]] = []
    for r in rules:
        b = blocks_by_id.get(int(r.content_block_id))
        if not b:
            continue

        if not _is_date_in_range(now=now_date, valid_from=r.valid_from, valid_to=r.valid_to):
            continue

        if r.channel and r.channel.strip().lower() != channel:
            continue

        if r.brand_id and int(r.brand_id) != int(brand_id or 0):
            continue

        if r.product_group_id and int(r.product_group_id) != int(product_group_id or 0):
            continue

        if r.product_id and int(r.product_id) != int(product_id):
            continue

        if r.category_id:
            if not category_id:
                continue
            if r.include_descendants:
                if int(r.category_id) not in category_ancestors:
                    continue
            else:
                if int(r.category_id) != int(category_id):
                    continue

        matched.append((r, b))

    # 3) Sort: rule.priority desc, then block.priority desc
    matched.sort(key=lambda rb: (-int(rb[0].priority or 0), -int(rb[1].priority or 0)))

    # 4) Exclusive: if first match is exclusive, keep only rules with same priority for this placement
    if matched and bool(getattr(matched[0][0], "is_exclusive", False)):
        top_pri = int(matched[0][0].priority or 0)
        matched = [rb for rb in matched if int(rb[0].priority or 0) == top_pri]

    # 5) Load translations for all blocks in result + any global blocks with no rules
    # Global blocks should be included even if no rules exist.
    included_block_ids: list[int] = []
    seen_block_ids: set[int] = set()

    # Add global blocks first (sorted by block priority)
    global_blocks = [b for b in blocks if b.placement == ContentBlock.Placement.GLOBAL]
    global_blocks.sort(key=lambda b: (-int(b.priority or 0), b.key))
    for b in global_blocks:
        if int(b.id) in seen_block_ids:
            continue
        included_block_ids.append(int(b.id))
        seen_block_ids.add(int(b.id))

    # Then add matched blocks
    for _r, b in matched:
        if int(b.id) in seen_block_ids:
            continue
        included_block_ids.append(int(b.id))
        seen_block_ids.add(int(b.id))

    fallback_langs = _translation_fallback_chain(language_code)

    translations = list(
        ContentBlockTranslation.objects.filter(
            content_block_id__in=included_block_ids,
            language_code__in=fallback_langs,
        ).only("content_block_id", "language_code", "title", "payload", "markdown")
    )

    # Pick best translation per block based on fallback order
    order_index = {lang: i for i, lang in enumerate(fallback_langs)}
    best_by_block: dict[int, ContentBlockTranslation] = {}
    for t in translations:
        bid = int(t.content_block_id)
        idx = order_index.get((t.language_code or "").lower(), 10_000)
        cur = best_by_block.get(bid)
        if cur is None:
            best_by_block[bid] = t
            continue
        cur_idx = order_index.get((cur.language_code or "").lower(), 10_000)
        if idx < cur_idx:
            best_by_block[bid] = t

    resolved: list[ContentBlockResolved] = []
    for bid in included_block_ids:
        b = blocks_by_id.get(int(bid))
        if not b:
            continue
        t = best_by_block.get(int(bid))
        payload = {}
        title = ""
        if t is not None:
            title = getattr(t, "title", "") or ""
            payload = getattr(t, "payload", None) or {}
            # Safety: if payload empty but markdown filled (older rows), expose markdown
            if not payload and getattr(t, "markdown", ""):
                payload = {"markdown": t.markdown or ""}

        resolved.append(
            ContentBlockResolved(
                key=b.key,
                title=title,
                placement=b.placement,
                type=b.type,
                payload=payload,
            )
        )

    cache.set(cache_key, resolved, timeout=cache_seconds)
    return resolved


def get_content_blocks_by_keys(
    *,
    site_id: int | None = None,
    keys: list[str],
    language_code: str | None = None,
    now: date | None = None,
    cache_seconds: int = 120,
) -> list[ContentBlockResolved]:
    now_date = now or date.today()

    normalized_keys = [(k or "").strip() for k in (keys or [])]
    normalized_keys = [k for k in normalized_keys if k]
    if not normalized_keys:
        return []

    site_id_v = int(site_id) if site_id is not None else 0
    keys_part = ",".join(sorted(set(normalized_keys)))

    cache_key = (
        f"content_blocks_by_keys:v1:site:{site_id_v}:keys:{keys_part}:"
        f"lang:{(language_code or '').lower()}:d:{now_date.isoformat()}"
    )
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    qs = ContentBlock.objects.filter(
        is_active=True,
        placement=ContentBlock.Placement.GLOBAL,
        key__in=normalized_keys,
    )
    if site_id_v:
        qs = qs.filter(site_id=site_id_v)

    blocks = list(
        qs.only(
            "id",
            "key",
            "type",
            "placement",
            "priority",
            "valid_from",
            "valid_to",
        )
    )
    blocks = [
        b
        for b in blocks
        if _is_date_in_range(now=now_date, valid_from=b.valid_from, valid_to=b.valid_to)
    ]
    blocks_by_id = {int(b.id): b for b in blocks}

    fallback_langs = _translation_fallback_chain(language_code)
    translations = list(
        ContentBlockTranslation.objects.filter(
            content_block_id__in=list(blocks_by_id.keys()),
            language_code__in=fallback_langs,
        ).only("content_block_id", "language_code", "title", "payload", "markdown")
    )

    order_index = {lang: i for i, lang in enumerate(fallback_langs)}
    best_by_block: dict[int, ContentBlockTranslation] = {}
    for t in translations:
        bid = int(t.content_block_id)
        idx = order_index.get((t.language_code or "").lower(), 10_000)
        cur = best_by_block.get(bid)
        if cur is None:
            best_by_block[bid] = t
            continue
        cur_idx = order_index.get((cur.language_code or "").lower(), 10_000)
        if idx < cur_idx:
            best_by_block[bid] = t

    def _sort_key(b: ContentBlock):
        return (-int(getattr(b, "priority", 0) or 0), (getattr(b, "key", "") or ""))

    blocks.sort(key=_sort_key)

    resolved: list[ContentBlockResolved] = []
    for b in blocks:
        bid = int(b.id)
        t = best_by_block.get(bid)
        payload = {}
        title = ""
        if t is not None:
            title = getattr(t, "title", "") or ""
            payload = getattr(t, "payload", None) or {}
            if not payload and getattr(t, "markdown", ""):
                payload = {"markdown": t.markdown or ""}

        resolved.append(
            ContentBlockResolved(
                key=b.key,
                title=title,
                placement=b.placement,
                type=b.type,
                payload=payload,
            )
        )

    cache.set(cache_key, resolved, timeout=cache_seconds)
    return resolved
