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

from .models import (
    ProductCooccur,
    ProductMomentum,
    ProductRelation,
    SeasonalBoost,
)

DEFAULT_KINDS = (ProductRelation.Kind.CROSS_SELL,)

_BOOST_CACHE_KEY = "recs:boosts:v1"
_BOOST_CACHE_SECONDS = 30


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
        .prefetch_related(
            "images",
            "translations",
            "brand__translations",
            "category__translations",
        )
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
    ).values("product_a_id", "product_b_id", "score", "order_count")
    scores: Counter = Counter()
    for r in rows:
        other = (
            r["product_b_id"]
            if r["product_a_id"] in seed_ids
            else r["product_a_id"]
        )
        if other not in seed_ids:
            # `score` (cosine-lift) dominuoja; `order_count` — fallback
            # eilutėms, sukurtoms prieš score backfill'ą.
            scores[other] += r["score"] or r["order_count"]
    return scores


def _active_boosts() -> list[SeasonalBoost]:
    """Aktyvios sezoninės taisyklės (scheduled langas arba manual trigger).

    Cache ~30s — admin'o manual trigger'iai („pasirodė stintos") beveik
    akimirksniu persikelia į ranking'ą be jokio rebuild'o.
    """
    from django.core.cache import cache

    rows = cache.get(_BOOST_CACHE_KEY)
    if rows is None:
        rows = [
            b for b in SeasonalBoost.objects.filter(is_active=True)
            .select_related("activity", "category")
            if b.is_currently_active()
        ]
        cache.set(_BOOST_CACHE_KEY, rows, _BOOST_CACHE_SECONDS)
    return list(rows)


def invalidate_boost_cache(**kwargs) -> None:
    from django.core.cache import cache

    cache.delete(_BOOST_CACHE_KEY)


def context_multipliers(product_ids) -> dict[int, float]:
    """Sezoninių boost'ų × momentum multiplikatoriai kandidatams.

    Grąžina {product_id: multiplier} — tik tie, kurie != 1.0. Taikoma
    request-time re-rank'e (Tier 2/3 + popular), tad manual trigger'io
    efektas matomas iškart, ne po naktinio rebuild'o.
    """
    ids = list(dict.fromkeys(int(i) for i in product_ids if i))
    if not ids:
        return {}

    boosts = _active_boosts()
    momentum = {
        int(m.product_id): float(m.momentum)
        for m in ProductMomentum.objects.filter(product_id__in=ids)
    }
    if not boosts and not momentum:
        return {}

    act_of: dict[int, set[int]] = {}
    parent_of: dict[int, int | None] = {}
    prod_cat: dict[int, tuple] = {}
    boost_cat_coords: dict[int, tuple] = {}

    if boosts:
        from catalog.models import Activity, Category, ProductActivity

        for pid, aid in ProductActivity.objects.filter(
            product_id__in=ids
        ).values_list("product_id", "activity_id"):
            act_of.setdefault(int(pid), set()).add(int(aid))

        needed_acts = {a for s in act_of.values() for a in s}
        needed_acts |= {b.activity_id for b in boosts if b.activity_id}
        for a in Activity.objects.filter(id__in=needed_acts).only("id", "parent_id"):
            parent_of[int(a.id)] = a.parent_id

        boost_cats = {b.category_id for b in boosts if b.category_id}
        if boost_cats:
            for cid, tid, lft, rght in Category.objects.filter(
                id__in=boost_cats
            ).values_list("id", "tree_id", "lft", "rght"):
                boost_cat_coords[int(cid)] = (int(tid), int(lft), int(rght))
            for pid, tid, lft, rght in Product.objects.filter(
                id__in=ids, category__isnull=False
            ).values_list("id", "category__tree_id", "category__lft",
                          "category__rght"):
                prod_cat[int(pid)] = (int(tid), int(lft), int(rght))

    def _matches(pid: int, b: SeasonalBoost) -> bool:
        if b.activity_id:
            prod_acts = act_of.get(pid) or set()
            if int(b.activity_id) in prod_acts:
                return True
            # Boost'as ant srities (parent) apima visas jos veiklas.
            if any(parent_of.get(a) == int(b.activity_id) for a in prod_acts):
                return True
        if b.category_id:
            bc = boost_cat_coords.get(int(b.category_id))
            pc = prod_cat.get(pid)
            if bc and pc and pc[0] == bc[0] and pc[1] >= bc[1] and pc[2] <= bc[2]:
                return True
        return False

    out: dict[int, float] = {}
    for pid in ids:
        mult = 1.0
        for b in boosts:
            if _matches(pid, b):
                mult *= float(b.boost)
        mult *= momentum.get(pid, 1.0)
        if mult != 1.0:
            out[pid] = mult
    return out


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

    base = _base_product_qs(shop_id=shop_id).exclude(id__in=set(exclude))

    # Pool susiaurinamas prieš brangias anotacijas — prekės, kurios
    # nesutampa nė vienu seed attribut'u, ir taip gautų 0 score'ų.
    pool_q = Q()
    if seed_cats:
        pool_q |= Q(category_id__in=seed_cats)
    if seed_brands:
        pool_q |= Q(brand_id__in=seed_brands)
    if seed_groups:
        pool_q |= Q(group_id__in=seed_groups)
    if seed_fv_ids:
        pool_q |= Q(feature_values__feature_value_id__in=seed_fv_ids)

    def _ranked(qs) -> list[int]:
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

    if pool_q:
        narrowed = base.filter(pool_q).distinct()
        ids = _ranked(narrowed)
        if len(ids) < limit * 3:
            # Reta atvejais pool'as per mažas — papildome globaliu
            # in-stock sąrašu (tail'as ir taip turėtų 0 score'ų).
            ids += _ranked(base.exclude(id__in=ids))[: limit * 3 - len(ids)]
        return ids[: limit * 3]
    return _ranked(base)


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

    def _ensure_cat_map(ids) -> None:
        """Bulk-load category_id visam kandidatų batch'ui — viena
        užklausa vietoj per-id N+1."""
        missing = [int(i) for i in ids if int(i) not in cat_of]
        if not missing:
            return
        for pid, cid in Product.objects.filter(id__in=missing).values_list(
            "id", "category_id"
        ):
            cat_of[int(pid)] = cid

    def take(ids, *, apply_cap=True):
        ids = list(ids)
        if apply_cap and diversify:
            _ensure_cat_map(ids)
        for pid in ids:
            if len(picked) >= limit:
                return
            pid = int(pid)
            if pid in picked_set:
                continue
            if apply_cap and diversify:
                cid = cat_of.get(pid)
                if cid and cat_counts[cid] >= per_category_cap:
                    continue
                cat_counts[cid] += 1
            picked.append(pid)
            picked_set.add(pid)

    # Tier 1: manual relations (admin intent — no diversity cap)
    manual_ids = _manual_relation_ids(seed_ids=seed_ids, kinds=kinds)
    take(_in_stock_ids_ordered(manual_ids, shop_id=shop_id, channel=channel),
         apply_cap=False)

    # Tier 2: purchase co-occurrence × sezoninis kontekstas
    if len(picked) < limit:
        scores = _cooccur_scores(seed_ids=seed_ids)
        if scores:
            mult = context_multipliers(list(scores))
            if mult:
                for pid in list(scores):
                    scores[pid] *= mult.get(pid, 1.0)
        co_ids = [pid for pid, _ in scores.most_common()]
        take(_in_stock_ids_ordered(co_ids, shop_id=shop_id, channel=channel))

    # Tier 3: feature/group/category/brand fallback × sezoninis kontekstas
    if len(picked) < limit:
        fallback = _fallback_scored_ids(
            seeds=seeds, seed_ids=seed_ids,
            exclude=picked_set, shop_id=shop_id,
            channel=channel, limit=limit,
        )
        mult = context_multipliers(fallback)
        if mult:
            # Stabilus sort'as: boost'intos prekės kyla fallback eilėje,
            # lygūs multiplikatoriai išsaugo originalų ranking'ą.
            fallback = sorted(
                fallback, key=lambda pid: -mult.get(pid, 1.0))
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


def _recent_sales_map(*, days: int) -> dict[int, int]:
    """Parduotų vnt. per produktą per `days` dienų (paid užsakymai)."""
    from checkout.models import Order, OrderLine

    since = timezone.now() - timezone.timedelta(days=days)
    rows = (
        OrderLine.objects
        .filter(
            order__status=Order.Status.PAID,
            order__created_at__gte=since,
            variant__isnull=False,
        )
        .values("variant__product_id")
        .annotate(q=Sum("qty"))
    )
    return {int(r["variant__product_id"]): int(r["q"] or 0) for r in rows}


def popular_product_ids(*, shop_id, channel, limit, exclude_ids=()) -> list[int]:
    """Cold-start list: dabartinės paklausos in-stock prekės.

    Ranking'as: paskutinių RECOMMENDATION_POPULAR_LOOKBACK_DAYS pardavimai
    × sezoninis kontekstas, tiebreak'as — bendras co-purchase score
    (× kontekstas). Anksčiau pildyta abėcėlės tvarka — rodė nieko
    nenusakančias prekes.
    """
    exclude = {int(i) for i in exclude_ids}
    lookback = int(getattr(
        settings, "RECOMMENDATION_POPULAR_LOOKBACK_DAYS", 30))

    co_scores: Counter = Counter()
    for r in ProductCooccur.objects.values(
        "product_a_id", "product_b_id", "score", "order_count"
    ):
        w = r["score"] or r["order_count"]
        co_scores[r["product_a_id"]] += w
        co_scores[r["product_b_id"]] += w

    sales = _recent_sales_map(days=lookback)
    mult = context_multipliers(set(co_scores) | set(sales))

    pool = (set(co_scores) | set(sales)) - exclude
    ordered = sorted(
        pool,
        key=lambda pid: (
            -(sales.get(pid, 0) * mult.get(pid, 1.0)),
            -(co_scores.get(pid, 0) * mult.get(pid, 1.0)),
            pid,
        ),
    )
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

    Kiekvienas užsakymas sveriamas eksponentiniu time decay
    (0.5^(amžius/RECOMMENDATION_HALF_LIFE_DAYS)) — švieži sezoniniai
    trendai nugali senus. Poros gauna tris matus:
      - order_count: žalias porų kiekis (admin matomumui),
      - weight: decay'd intas bendras co-purchase svoris,
      - score: cosine-lift = weight / sqrt(orders_a * orders_b) —
        normalizuoja bestseller bias (populiari prekė kartu su viskuo).

    Also refreshes auto ProductRelation(cross_sell) rows: top-N co-purchased
    neighbours per product become visible/editable in admin.
    """
    from checkout.models import Order, OrderLine

    if lookback_days is None:
        lookback_days = int(getattr(
            settings, "RECOMMENDATION_LOOKBACK_DAYS", 180))
    half_life = float(getattr(
        settings, "RECOMMENDATION_HALF_LIFE_DAYS", 60) or 60)
    since = timezone.now() - timezone.timedelta(days=lookback_days)

    rows = (
        OrderLine.objects
        .filter(
            order__status=Order.Status.PAID,
            order__created_at__gte=since,
            variant__isnull=False,
        )
        .values("order_id", "order__created_at", "variant__product_id")
        .distinct()
    )
    now = timezone.now()
    order_weight: dict[int, float] = {}
    products_per_order: dict[int, set[int]] = {}
    for r in rows:
        oid = r["order_id"]
        w = order_weight.get(oid)
        if w is None:
            age_days = max(
                0.0, (now - r["order__created_at"]).total_seconds() / 86400)
            w = 0.5 ** (age_days / half_life)
            order_weight[oid] = w
        products_per_order.setdefault(oid, set()).add(
            r["variant__product_id"])

    pair_counts: Counter = Counter()
    pair_weight: Counter = Counter()
    product_orders: Counter = Counter()
    for oid, products in products_per_order.items():
        w = order_weight[oid]
        for pid in products:
            product_orders[pid] += w
        if len(products) < 2:
            continue
        for a, b in combinations(sorted(products), 2):
            pair_counts[(a, b)] += 1
            pair_weight[(a, b)] += w

    pair_score: dict[tuple[int, int], float] = {}
    for (a, b), w in pair_weight.items():
        denom = (product_orders[a] * product_orders[b]) ** 0.5
        pair_score[(a, b)] = w / denom if denom else 0.0

    neighbours: dict[int, Counter] = {}
    for (a, b), sc in pair_score.items():
        neighbours.setdefault(a, Counter())[b] = sc
        neighbours.setdefault(b, Counter())[a] = sc

    with transaction.atomic():
        ProductCooccur.objects.all().delete()
        ProductCooccur.objects.bulk_create([
            ProductCooccur(
                product_a_id=a, product_b_id=b,
                order_count=pair_counts[(a, b)],
                weight=pair_weight[(a, b)],
                score=pair_score[(a, b)],
            )
            for (a, b) in pair_counts
        ])

        ProductRelation.objects.filter(
            source=ProductRelation.Source.AUTO).delete()
        auto_relations = []
        top_n = int(getattr(settings, "RECOMMENDATION_AUTO_TOP_N", 20))
        for src_id, nbrs in neighbours.items():
            for tgt_id, _sc in nbrs.most_common(top_n):
                auto_relations.append(ProductRelation(
                    source_product_id=src_id,
                    target_product_id=tgt_id,
                    kind=ProductRelation.Kind.CROSS_SELL,
                    source=ProductRelation.Source.AUTO,
                    # Žalias count'as — admin matomumas („kartu pirkta N kartų").
                    weight=pair_counts[tuple(sorted((src_id, tgt_id)))],
                ))
        ProductRelation.objects.bulk_create(
            auto_relations, ignore_conflicts=True)

    # bulk_create/bulk delete apeina signalus — recs listing cache
    # invaliduojamas rankiniu bump'u.
    try:
        from catalog.signals import bump_listing_ver

        bump_listing_ver()
    except Exception:
        pass

    return {
        "orders_scanned": len(products_per_order),
        "pairs": len(pair_counts),
        "auto_relations": len(auto_relations),
        "lookback_days": lookback_days,
        "half_life_days": half_life,
    }


def rebuild_momentum(*, recent_days: int | None = None,
                     baseline_days: int | None = None) -> dict:
    """Perproduktinis pardavimų momentum — sezoninių šuolių detektorius.

    momentum = (recent + 1) / (expected + 1), kur expected =
    baseline_daily × recent_days; clamp'intas [MIN, MAX]. Prekės be jokių
    pardavimų lange neturi eilutės → lookup'e skaitoma 1.0 (neutralu).

    „Staiga atšalo → ledas" scenarijus: poledinės įrangos pardavimai
    iššoka per kelias dienas, momentum pakyla iki MAX ir Tier 2/3 bei
    popular re-rank'as pasislenka be admin'o įsikišimo.
    """
    from checkout.models import Order, OrderLine

    if recent_days is None:
        recent_days = int(getattr(settings, "REC_MOMENTUM_RECENT_DAYS", 14))
    if baseline_days is None:
        baseline_days = int(getattr(settings, "REC_MOMENTUM_BASELINE_DAYS", 76))
    lo = float(getattr(settings, "REC_MOMENTUM_MIN", 0.5))
    hi = float(getattr(settings, "REC_MOMENTUM_MAX", 4.0))

    now = timezone.now()
    recent_since = now - timezone.timedelta(days=recent_days)
    window_since = now - timezone.timedelta(days=recent_days + baseline_days)

    rows = (
        OrderLine.objects
        .filter(
            order__status=Order.Status.PAID,
            order__created_at__gte=window_since,
            variant__isnull=False,
        )
        .annotate(
            _recent=Case(
                When(order__created_at__gte=recent_since, then=Value(1)),
                default=Value(0), output_field=IntegerField(),
            )
        )
        .values("variant__product_id", "_recent")
        .annotate(q=Sum("qty"))
    )

    recent: Counter = Counter()
    baseline: Counter = Counter()
    for r in rows:
        pid = int(r["variant__product_id"])
        if r["_recent"]:
            recent[pid] += int(r["q"] or 0)
        else:
            baseline[pid] += int(r["q"] or 0)

    objs = []
    for pid in set(recent) | set(baseline):
        baseline_daily = baseline[pid] / max(1, baseline_days)
        expected = baseline_daily * recent_days
        raw = (recent[pid] + 1.0) / (expected + 1.0)
        objs.append(ProductMomentum(
            product_id=pid,
            momentum=min(hi, max(lo, raw)),
            sales_recent=recent[pid],
            baseline_daily=baseline_daily,
        ))

    with transaction.atomic():
        ProductMomentum.objects.all().delete()
        ProductMomentum.objects.bulk_create(objs)

    return {
        "products": len(objs),
        "recent_days": recent_days,
        "baseline_days": baseline_days,
        "boosted": sum(1 for o in objs if o.momentum > 1.0),
        "demoted": sum(1 for o in objs if o.momentum < 1.0),
    }
