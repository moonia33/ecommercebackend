from __future__ import annotations

from collections import Counter
from itertools import combinations

from django.conf import settings
from django.db import transaction
from django.db.models import Case, Count, IntegerField, Min, Q, Sum, Value, When
from django.utils import timezone

from catalog.api.shared import list_price_exprs, offer_price_exprs
from catalog.models import InventoryItem, Product, ProductFeatureValue
from catalog.shop_scope import apply_shop_product_scope

from .models import ProductCooccur, ProductRelation

DEFAULT_KINDS = (ProductRelation.Kind.CROSS_SELL,)


def _visibility_for_channel(channel: str) -> str:
    return (
        InventoryItem.OfferVisibility.OUTLET
        if channel == "outlet"
        else InventoryItem.OfferVisibility.NORMAL
    )


def _annotate_listing(qs, *, channel: str):
    min_price_expr, max_price_expr = list_price_exprs()
    offer_expr, offer_filter = offer_price_exprs(_visibility_for_channel(channel))
    return qs.annotate(
        _min_variant_price=min_price_expr,
        _max_variant_price=max_price_expr,
        _min_offer_price=Min(offer_expr, filter=offer_filter),
    )


def _base_product_qs(*, shop_id):
    qs = (
        Product.objects.filter(is_active=True)
        .select_related("brand", "category", "tax_class")
        .prefetch_related("images")
    )
    return apply_shop_product_scope(qs=qs, shop_id=shop_id)


def _in_stock_ids(ids, *, shop_id, channel) -> set[int]:
    ids = [int(i) for i in ids if i]
    if not ids:
        return set()
    qs = _base_product_qs(shop_id=shop_id).filter(id__in=ids)
    qs = _annotate_listing(qs, channel=channel)
    return {
        p.id for p in qs
        if getattr(p, "_min_offer_price", None) is not None
    }


def ordered_product_qs(*, ids, shop_id, channel):
    """Listing-annotated queryset preserving the given id order."""
    ids = list(dict.fromkeys(int(i) for i in ids))
    qs = _base_product_qs(shop_id=shop_id).filter(id__in=ids)
    qs = _annotate_listing(qs, channel=channel)
    ordering = Case(
        *[When(id=pid, then=Value(pos)) for pos, pid in enumerate(ids)],
        output_field=IntegerField(),
    )
    return qs.annotate(_rec_pos=ordering).order_by("_rec_pos")


def _manual_relation_ids(*, seed_ids, kinds) -> list[int]:
    rows = (
        ProductRelation.objects
        .filter(
            source=ProductRelation.Source.MANUAL,
            kind__in=kinds,
            source_product_id__in=seed_ids,
        )
        .values("target_product_id")
        .annotate(w=Sum("weight"))
        .order_by("-w", "target_product_id")
        .values_list("target_product_id", flat=True)
    )
    return list(dict.fromkeys(rows))


def _cooccur_scores(*, seed_ids) -> Counter:
    rows = ProductCooccur.objects.filter(
        Q(product_a_id__in=seed_ids) | Q(product_b_id__in=seed_ids)
    ).values("product_a_id", "product_b_id", "order_count")
    scores: Counter = Counter()
    for r in rows:
        other = (
            r["product_b_id"]
            if r["product_a_id"] in seed_ids
            else r["product_a_id"]
        )
        if other not in seed_ids:
            scores[other] += r["order_count"]
    return scores


def _fallback_scored_ids(*, seeds, seed_ids, exclude, shop_id, channel, limit) -> list[int]:
    """Feature/group/category/brand-ranked in-stock candidates."""
    seed_fv_ids = set(
        ProductFeatureValue.objects.filter(
            product_id__in=seed_ids,
            feature__recommendation_boost=True,
        ).values_list("feature_value_id", flat=True)
    )
    seed_cats = {p.category_id for p in seeds if p.category_id}
    seed_brands = {p.brand_id for p in seeds if p.brand_id}
    seed_groups = {p.group_id for p in seeds if p.group_id}

    qs = (
        _base_product_qs(shop_id=shop_id)
        .exclude(id__in=set(exclude))
    )
    qs = _annotate_listing(qs, channel=channel).filter(
        _min_offer_price__isnull=False
    )

    if seed_fv_ids:
        feature_expr = Count(
            "feature_values",
            filter=Q(
                feature_values__feature_value_id__in=seed_fv_ids,
                feature_values__feature__recommendation_boost=True,
            ),
            distinct=True,
        )
    else:
        feature_expr = Value(0, output_field=IntegerField())

    qs = qs.annotate(
        _feature_match=feature_expr,
        _same_group=Case(
            When(group_id__in=seed_groups, then=Value(1)),
            default=Value(0), output_field=IntegerField(),
        ) if seed_groups else Value(0, output_field=IntegerField()),
        _same_category=Case(
            When(category_id__in=seed_cats, then=Value(1)),
            default=Value(0), output_field=IntegerField(),
        ) if seed_cats else Value(0, output_field=IntegerField()),
        _same_brand=Case(
            When(brand_id__in=seed_brands, then=Value(1)),
            default=Value(0), output_field=IntegerField(),
        ) if seed_brands else Value(0, output_field=IntegerField()),
    )
    qs = qs.order_by(
        "-_feature_match", "-_same_group",
        "-_same_category", "-_same_brand", "name", "id",
    )
    return [p.id for p in qs[: limit * 3]]


def recommend_product_ids(
    *,
    seed_products,
    kinds=DEFAULT_KINDS,
    shop_id,
    channel: str,
    limit: int,
    exclude_ids=(),
    diversify: bool = True,
) -> list[int]:
    """Ranked product ids: manual relations -> co-purchase -> feature fallback."""
    seeds = list(seed_products or [])
    seed_ids = {int(p.id) for p in seeds}
    exclude = set(int(i) for i in exclude_ids) | seed_ids
    limit = max(0, int(limit))
    if limit == 0:
        return []
    if not seed_ids:
        return popular_product_ids(
            shop_id=shop_id, channel=channel,
            limit=limit, exclude_ids=exclude,
        )

    picked: list[int] = []
    picked_set: set[int] = set(exclude)
    per_category_cap = max(3, (limit + 2) // 3)
    cat_counts: Counter = Counter()
    cat_of: dict[int, int] = {}

    def category_of(pid: int):
        if pid not in cat_of:
            cat_of[pid] = (
                Product.objects.filter(id=pid)
                .values_list("category_id", flat=True).first()
            )
        return cat_of[pid]

    def take(ids, *, apply_cap=True):
        for pid in ids:
            if len(picked) >= limit:
                return
            pid = int(pid)
            if pid in picked_set:
                continue
            if apply_cap and diversify:
                cid = category_of(pid)
                if cid and cat_counts[cid] >= per_category_cap:
                    continue
                cat_counts[cid] += 1
            picked.append(pid)
            picked_set.add(pid)

    # Tier 1: manual relations (admin intent — no diversity cap)
    manual_ids = _manual_relation_ids(seed_ids=seed_ids, kinds=kinds)
    take(_in_stock_ids_ordered(manual_ids, shop_id=shop_id, channel=channel),
         apply_cap=False)

    # Tier 2: purchase co-occurrence
    if len(picked) < limit:
        scores = _cooccur_scores(seed_ids=seed_ids)
        co_ids = [pid for pid, _ in scores.most_common()]
        take(_in_stock_ids_ordered(co_ids, shop_id=shop_id, channel=channel))

    # Tier 3: feature/group/category/brand fallback
    if len(picked) < limit:
        fallback = _fallback_scored_ids(
            seeds=seeds, seed_ids=seed_ids,
            exclude=picked_set, shop_id=shop_id,
            channel=channel, limit=limit,
        )
        take(fallback)
        # Relax the cap if still short
        if len(picked) < limit:
            take(fallback, apply_cap=False)

    return picked


def _in_stock_ids_ordered(ids, *, shop_id, channel) -> list[int]:
    if not ids:
        return []
    avail = _in_stock_ids(ids, shop_id=shop_id, channel=channel)
    return [int(i) for i in ids if int(i) in avail]


def popular_product_ids(*, shop_id, channel, limit, exclude_ids=()) -> list[int]:
    """Cold-start list: globally most co-purchased in-stock products."""
    exclude = {int(i) for i in exclude_ids}
    scores: Counter = Counter()
    for r in ProductCooccur.objects.values(
        "product_a_id", "product_b_id", "order_count"
    ):
        scores[r["product_a_id"]] += r["order_count"]
        scores[r["product_b_id"]] += r["order_count"]
    ordered = [pid for pid, _ in scores.most_common() if pid not in exclude]
    out = _in_stock_ids_ordered(ordered, shop_id=shop_id, channel=channel)[:limit]
    if len(out) < limit:
        qs = _base_product_qs(shop_id=shop_id).exclude(
            id__in=set(out) | exclude)
        qs = _annotate_listing(qs, channel=channel).filter(
            _min_offer_price__isnull=False
        ).order_by("name", "id")
        out += [p.id for p in qs[: limit - len(out)]]
    return out[:limit]


def rebuild_cooccurrence(*, lookback_days: int | None = None) -> dict:
    """Rebuild the co-purchase matrix from paid orders (order-level pairs).

    Also refreshes auto ProductRelation(cross_sell) rows: top-N co-purchased
    neighbours per product become visible/editable in admin.
    """
    from checkout.models import Order, OrderLine

    if lookback_days is None:
        lookback_days = int(getattr(
            settings, "RECOMMENDATION_LOOKBACK_DAYS", 180))
    since = timezone.now() - timezone.timedelta(days=lookback_days)

    rows = (
        OrderLine.objects
        .filter(
            order__status=Order.Status.PAID,
            order__created_at__gte=since,
            variant__isnull=False,
        )
        .values("order_id", "variant__product_id")
        .distinct()
    )
    products_per_order: dict[int, set[int]] = {}
    for r in rows:
        products_per_order.setdefault(r["order_id"], set()).add(
            r["variant__product_id"])

    pair_counts: Counter = Counter()
    for products in products_per_order.values():
        if len(products) < 2:
            continue
        for a, b in combinations(sorted(products), 2):
            pair_counts[(a, b)] += 1

    neighbours: dict[int, Counter] = {}
    for (a, b), cnt in pair_counts.items():
        neighbours.setdefault(a, Counter())[b] = cnt
        neighbours.setdefault(b, Counter())[a] = cnt

    with transaction.atomic():
        ProductCooccur.objects.all().delete()
        ProductCooccur.objects.bulk_create([
            ProductCooccur(product_a_id=a, product_b_id=b, order_count=cnt)
            for (a, b), cnt in pair_counts.items()
        ])

        ProductRelation.objects.filter(
            source=ProductRelation.Source.AUTO).delete()
        auto_relations = []
        top_n = int(getattr(settings, "RECOMMENDATION_AUTO_TOP_N", 20))
        for src_id, nbrs in neighbours.items():
            for tgt_id, cnt in nbrs.most_common(top_n):
                auto_relations.append(ProductRelation(
                    source_product_id=src_id,
                    target_product_id=tgt_id,
                    kind=ProductRelation.Kind.CROSS_SELL,
                    source=ProductRelation.Source.AUTO,
                    weight=cnt,
                ))
        ProductRelation.objects.bulk_create(
            auto_relations, ignore_conflicts=True)

    return {
        "orders_scanned": len(products_per_order),
        "pairs": len(pair_counts),
        "auto_relations": len(auto_relations),
        "lookback_days": lookback_days,
    }
