from __future__ import annotations

from decimal import Decimal

from django.db.models import Case, Count, IntegerField, Min, Q, Value, When
from ninja.errors import HttpError

from .shared import (
    _money_out,
    _shop_or_404_when_strict,
    _validate_country_and_channel,
    _visibility_for_channel,
    apply_catalog_filters,
    apply_products_sorting,
    apply_shop_product_scope,
    build_facets_payload,
    build_product_detail_payload,
    get_request_language_code,
    list_price_exprs,
    offer_price_exprs,
    resolve_product_by_slug,
    router,
    serialize_products_listing,
    track_event,
)
from ..models import Product
from ..localization import localize_brand, localize_product
from ..services.catalog_core import parse_range_pairs
from ..services.image_out import image_out
from pricing.services import get_vat_rate
from ..api_schemas import CatalogFacetsOut, ProductDetailOut, ProductPageOut, SuggestItemOut


def _serialize_product_page(
    *,
    qs,
    page: int,
    page_size: int,
    country_code: str,
    channel: str,
    shop_id: int | None = None,
    language_code: str | None,
) -> ProductPageOut:
    """Count + slice BEFORE serialization — never serialize the whole table."""
    page = max(1, int(page or 1))
    page_size = max(1, min(int(page_size or 20), 100))
    offset = (page - 1) * page_size
    return ProductPageOut(
        count=qs.count(),
        items=serialize_products_listing(
            qs=qs[offset:offset + page_size],
            country_code=country_code,
            channel=channel,
            shop_id=shop_id,
            language_code=language_code,
        ),
    )


def _engine_browse(
    *,
    q,
    shop_id,
    channel,
    category_slug,
    brand_slug,
    group_code,
    feature,
    option,
    range_filter,
    sort,
    in_stock_only,
    collection,
    page,
    page_size,
    language_code,
):
    """Pilnas browse/search per Typesense → (ids, total_found).

    Slug'ai rezolvuojami čia — tie patys 404 semantikos kaip
    apply_catalog_filters. None = engine nepasiekiamas → ORM fallback.
    """
    from django.conf import settings as dj_settings

    if getattr(dj_settings, "TESTING", False):
        return None

    from search.services import browse_products
    from ..services.catalog_core import parse_pairs, parse_range_pairs
    from ..models import ProductGroup
    from ..localization import resolve_brand_by_slug, resolve_category_by_slug

    category_id = None
    if category_slug:
        cat = resolve_category_by_slug(
            slug=category_slug, language_code=language_code, is_active=True
        )
        if not cat:
            raise HttpError(404, "Category not found")
        category_id = int(cat.id)

    brand_id = None
    if brand_slug:
        brand = resolve_brand_by_slug(
            slug=brand_slug, language_code=language_code, is_active=True
        )
        if not brand:
            raise HttpError(404, "Brand not found")
        brand_id = int(brand.id)

    group_id = None
    if group_code:
        group = ProductGroup.objects.filter(code=group_code, is_active=True).first()
        if not group:
            raise HttpError(404, "Product group not found")
        group_id = int(group.id)

    return browse_products(
        q=q,
        shop_id=shop_id,
        channel=channel,
        category_id=category_id,
        brand_id=brand_id,
        group_id=group_id,
        feature_pairs=parse_pairs(feature) if feature else None,
        option_pairs=parse_pairs(option) if option else None,
        ranges=parse_range_pairs(range_filter) if range_filter else None,
        sort=sort,
        in_stock_only=in_stock_only,
        collection=collection,
        page=page,
        page_size=page_size,
    )


def _ordered_listing_qs(*, ids, qs_base):
    """Base qs (su anotacijomis) sužymėtas paduota id tvarka."""
    ids = list(dict.fromkeys(int(i) for i in ids))
    ordering = Case(
        *[When(id=pid, then=Value(pos)) for pos, pid in enumerate(ids)],
        output_field=IntegerField(),
    )
    return qs_base.filter(id__in=ids).annotate(_rec_pos=ordering).order_by("_rec_pos")


def _apply_search_engine(qs, *, q, shop_id, channel, sort, range_filter=None):
    """Resolve q via Typesense when configured.

    Returns (qs, remaining_q, engine_ordered). When Typesense answers, qs is
    restricted to the hit ids in engine order and q is consumed (None) so the
    ORM icontains filter is skipped. When Typesense is unavailable, returns
    (qs, q, False) unchanged — the ORM path handles q.

    `range` params are pushed into the Typesense filter_by as a pre-filter;
    the ORM range filter still applies on top, so semantics stay exact.
    """
    qv = (q or "").strip()
    if not qv:
        return qs, q, False

    from search.services import SORT_MAP, search_product_ids

    ranges = parse_range_pairs(range_filter) if range_filter else None
    ids = search_product_ids(
        q=qv, shop_id=shop_id, channel=channel, sort=sort, ranges=ranges)
    if ids is None:
        return qs, q, False
    if not ids:
        return qs.none(), None, True

    ordering = Case(
        *[When(id=pid, then=Value(pos)) for pos, pid in enumerate(ids)],
        output_field=IntegerField(),
    )
    qs = (
        qs.filter(id__in=ids)
        .annotate(_q_pos=ordering)
        .order_by("_q_pos")
    )
    engine_sorted = not (sort or "").strip() or (
        sort.strip().lower() in SORT_MAP)
    return qs, None, engine_sorted


def _canonical_csv(value: str | None) -> str | None:
    """Filtro reikšmių tvarka rezultato nekeičia — cache raktui
    `size:6,size:4` ir `size:4,size:6` turi sutapti."""
    if not value:
        return value
    return ",".join(sorted({p.strip() for p in value.split(",") if p.strip()}))


@router.get("/products", response=ProductPageOut)
def products(
    request,
    country_code: str = "LT",
    channel: str = "normal",
    language_code: str | None = None,
    q: str | None = None,
    category_slug: str | None = None,
    brand_slug: str | None = None,
    group_code: str | None = None,
    feature: str | None = None,
    option: str | None = None,
    range: str | None = None,
    sort: str | None = None,
    in_stock_only: bool = False,
    collection: str | None = None,
    page: int = 1,
    page_size: int = 20,
):
    country_code, channel = _validate_country_and_channel(
        country_code=country_code, channel=channel
    )
    if language_code is None:
        language_code = get_request_language_code(request)

    visibility = _visibility_for_channel(channel)
    min_price_expr, max_price_expr = list_price_exprs()
    offer_price_expr, offer_filter = offer_price_exprs(visibility)
    min_offer_price_expr = Min(offer_price_expr, filter=offer_filter)

    shop = _shop_or_404_when_strict(request)
    shop_id = int(shop.id) if shop else None
    collection = (collection or "").strip().lower() or None
    if collection not in {None, "new", "sale"}:
        raise HttpError(400, "Unsupported collection (expected 'new' or 'sale')")
    page = max(1, int(page or 1))
    page_size = max(1, min(int(page_size or 20), 100))

    # Trumpas per-parametrų response cache — tą patį listing'ą mato visi
    # (promo ctx neper-user); struktūros pokyčiai invaliduojami per
    # `catalog:listing:ver`, stock drift'as — per TTL.
    import hashlib

    from django.conf import settings as dj_settings
    from django.core.cache import cache

    params_hash = hashlib.md5(
        "|".join(str(x) for x in (
            q, category_slug, brand_slug, group_code,
            _canonical_csv(feature), _canonical_csv(option), range, sort,
            bool(in_stock_only), collection, page, page_size,
        )).encode()
    ).hexdigest()
    listing_ver = cache.get("catalog:listing:ver", 1)
    cache_key = (
        f"catalog:listing:v1:{shop_id or 'x'}:{language_code}:"
        f"{country_code}:{channel}:{params_hash}:v{listing_ver}"
    )
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    def _cached(payload):
        cache.set(
            cache_key, payload,
            int(getattr(dj_settings, "LISTING_CACHE_SECONDS", 60)),
        )
        return payload

    # Pirminis kelias — Typesense browse: ids+found iš indekso, DB
    # anotacijos tik 1 puslapio prekėms. ORM fallback žemiau.
    engine = _engine_browse(
        q=q,
        shop_id=shop_id,
        channel=channel,
        category_slug=category_slug,
        brand_slug=brand_slug,
        group_code=group_code,
        feature=feature,
        option=option,
        range_filter=range,
        sort=sort,
        in_stock_only=in_stock_only,
        collection=collection,
        page=page,
        page_size=page_size,
        language_code=language_code,
    )
    if engine is not None:
        ids, found = engine
        qs_page = _ordered_listing_qs(
            ids=ids,
            qs_base=(
                Product.objects.filter(is_active=True)
                .select_related("brand", "category", "tax_class")
                .prefetch_related(
                    "images", "translations",
                    "brand__translations", "category__translations",
                )
                .annotate(_min_variant_price=min_price_expr)
                .annotate(_max_variant_price=max_price_expr)
                .annotate(_min_offer_price=min_offer_price_expr)
            ),
        )
        return _cached(
            ProductPageOut(
                count=found,
                items=serialize_products_listing(
                    qs=qs_page,
                    country_code=country_code,
                    channel=channel,
                    shop_id=shop_id,
                    language_code=language_code,
                ),
            )
        )

    qs = (
        Product.objects.filter(is_active=True)
        .select_related("brand", "category", "tax_class")
        .prefetch_related("images", "translations", "brand__translations", "category__translations")
        .annotate(_min_variant_price=min_price_expr)
        .annotate(_max_variant_price=max_price_expr)
        .annotate(_min_offer_price=min_offer_price_expr)
    )
    qs = apply_shop_product_scope(qs=qs, shop_id=shop_id)

    qs = qs.annotate(
        _has_stock=Case(
            When(_min_offer_price__isnull=False, then=Value(1)),
            default=Value(0),
            output_field=IntegerField(),
        )
    )

    if in_stock_only:
        qs = qs.filter(_has_stock=1)

    qs, q, engine_ordered = _apply_search_engine(
        qs, q=q, shop_id=shop_id, channel=channel, sort=sort,
        range_filter=range)

    if not engine_ordered:
        qs = apply_products_sorting(qs=qs, sort=sort)

    qs, _selected_category = apply_catalog_filters(
        qs=qs,
        q=q,
        category_slug=category_slug,
        brand_slug=brand_slug,
        language_code=language_code,
        group_code=group_code,
        feature=feature,
        option=option,
        range_filter=range,
    )

    if collection == "new":
        from django.utils import timezone as _tz

        qs = qs.filter(created_at__gte=_tz.now() - _tz.timedelta(days=30))
    elif collection == "sale":
        qs = qs.filter(_min_offer_price__lt=F("_min_variant_price"))

    if channel == "outlet":
        qs = qs.filter(_min_offer_price__isnull=False)

    return _cached(
        _serialize_product_page(
            qs=qs,
            page=page,
            page_size=page_size,
            country_code=country_code,
            channel=channel,
            shop_id=shop_id,
            language_code=language_code,
        )
    )


@router.get("/suggest", response=list[SuggestItemOut])
def product_suggest(
    request,
    q: str,
    country_code: str = "LT",
    channel: str = "normal",
    language_code: str | None = None,
):
    """Lengvas autocomplete: Typesense hits + <mark> highlight +
    minimalus ORM enrichment (brand, thumb, kaina). Pilnas listing
    serialization per daug sunkus 180ms debounce'ui."""
    q = (q or "").strip()
    if len(q) < 2:
        return []
    country_code, channel = _validate_country_and_channel(
        country_code=country_code, channel=channel
    )
    if language_code is None:
        language_code = get_request_language_code(request)
    shop = _shop_or_404_when_strict(request)
    shop_id = int(shop.id) if shop else None

    from search.services import search_suggest

    hits = search_suggest(q=q, shop_id=shop_id, channel=channel, limit=8)

    qs_base = (
        Product.objects.filter(is_active=True)
        .select_related("brand", "tax_class")
        .prefetch_related("images", "translations", "brand__translations")
        .annotate(
            _min_variant_price=Min(
                "variants__price_eur", filter=Q(variants__is_active=True)
            )
        )
    )
    if hits is not None:
        ids = [h["id"] for h in hits]
        by_id = {p.id: p for p in qs_base.filter(id__in=ids)}
        ordered = [by_id[i] for i in ids if i in by_id]
        html_by_id = {h["id"]: h for h in hits}
    else:
        # ORM fallback kai Typesense down — paprastas icontains.
        ordered = list(
            apply_shop_product_scope(
                qs=qs_base.filter(
                    Q(name__icontains=q) | Q(sku__icontains=q)),
                shop_id=shop_id,
            ).order_by("name")[:8]
        )
        html_by_id = {}

    vat_cache: dict[int, Decimal] = {}

    def _rate(p: Product) -> Decimal:
        key = int(p.tax_class_id or 0)
        if key not in vat_cache:
            try:
                vat_cache[key] = get_vat_rate(
                    country_code=country_code, tax_class=p.tax_class
                ) if p.tax_class_id else Decimal("0")
            except Exception:
                vat_cache[key] = Decimal("0")
        return vat_cache[key]

    out: list[SuggestItemOut] = []
    for p in ordered:
        product_loc = localize_product(p, language_code=language_code)
        h = html_by_id.get(p.id) or {}
        first = sorted(
            list(p.images.all()), key=lambda i: (i.sort_order, i.id))[:1]
        img = image_out(first[0], context="listing") if first else None
        price = (
            _money_out(
                currency="EUR",
                unit_net=Decimal(p._min_variant_price),
                vat_rate=_rate(p),
            )
            if p._min_variant_price is not None
            else None
        )
        out.append(SuggestItemOut(
            id=p.id,
            slug=product_loc["slug"],
            name=product_loc["name"],
            name_html=(h.get("name_html") or product_loc["name"]),
            sku=(h.get("sku") or p.sku or ""),
            sku_html=(h.get("sku_html") or ""),
            brand_name=(
                localize_brand(p.brand, language_code=language_code)["name"]
                if p.brand else ""
            ),
            image_url=(img or {}).get("url"),
            price=price,
        ))
    return out


@router.get("/products/facets", response=CatalogFacetsOut)
def product_facets(
    request,
    country_code: str = "LT",
    channel: str = "normal",
    language_code: str | None = None,
    q: str | None = None,
    category_slug: str | None = None,
    brand_slug: str | None = None,
    group_code: str | None = None,
    feature: str | None = None,
    option: str | None = None,
    range: str | None = None,
):
    country_code, channel = _validate_country_and_channel(
        country_code=country_code, channel=channel
    )
    if language_code is None:
        language_code = get_request_language_code(request)

    shop = _shop_or_404_when_strict(request)
    shop_id = int(shop.id) if shop else None

    # Facets skaičiavimas be filtrų brangus (visos feature/option reikšmės
    # per visą katalogą ~2.7s/300KB) — TTL cache pagal visus parametrus.
    # Ne real-time jautrus: naujos prekės/facet'ai atsiranda per TTL.
    import hashlib

    from django.conf import settings as dj_settings
    from django.core.cache import cache

    params_hash = hashlib.md5(
        "|".join(str(x) for x in (
            q, category_slug, brand_slug, group_code,
            _canonical_csv(feature), _canonical_csv(option), range,
        )).encode()
    ).hexdigest()
    facets_ver = cache.get("catalog:facets:ver", 1)
    cache_key = (
        f"catalog:facets:v1:{shop_id or 'x'}:{language_code}:"
        f"{country_code}:{channel}:{params_hash}:v{facets_ver}"
    )
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    # Typesense facet_by kelias: kai nėra feature/option/range filtrų,
    # used-set'ai (brand/category/group/feature values) imami iš indekso —
    # ORM distinct-scan'ai per visą katalogą praleidžiami.
    engine_counts = None
    selected_category = None
    if not (feature or option or range):
        from catalog.localization import (
            resolve_brand_by_slug, resolve_category_by_slug)
        from catalog.models import ProductGroup
        from catalog.services.catalog_core import descendant_category_ids
        from search.services import search_facet_counts

        extra = []
        if category_slug:
            selected_category = resolve_category_by_slug(
                slug=category_slug, language_code=language_code,
                is_active=True)
            if not selected_category:
                raise HttpError(404, "Category not found")
            ids = descendant_category_ids(root_id=int(selected_category.id))
            extra.append("category_ids:=[" + ",".join(str(i) for i in ids) + "]")
        if brand_slug:
            brand = resolve_brand_by_slug(
                slug=brand_slug, language_code=language_code, is_active=True)
            if not brand:
                raise HttpError(404, "Brand not found")
            extra.append(f"brand_id:={int(brand.id)}")
        if group_code:
            group = ProductGroup.objects.filter(
                code=group_code, is_active=True).first()
            if not group:
                raise HttpError(404, "Product group not found")
            extra.append(f"group_id:={int(group.id)}")

        engine_counts = search_facet_counts(
            q=q,
            shop_id=shop_id,
            channel=channel,
            facet_fields=["brand_id", "category_ids", "group_id", "feature_facets"],
            extra_filters=extra,
        )

    if engine_counts is not None:
        # Option type'ų indekse nėra — vienintelė ORM dalis lieka
        # product_ids subquery VOV skanui (lengva, ~0.1s).
        visibility = _visibility_for_channel(channel)
        _offer_price_expr, offer_filter = offer_price_exprs(visibility)
        qs = Product.objects.filter(is_active=True).annotate(
            _has_offer=Count("id", filter=offer_filter)
        )
        qs = apply_shop_product_scope(qs=qs, shop_id=shop_id)
        if channel == "outlet":
            qs = qs.filter(_has_offer__gt=0)
        qs, q, _engine_ordered = _apply_search_engine(
            qs, q=q, shop_id=shop_id, channel=channel, sort=None,
            range_filter=range)
        qs, _sc = apply_catalog_filters(
            qs=qs, q=q, category_slug=category_slug,
            brand_slug=brand_slug, language_code=language_code,
            group_code=group_code, feature=feature, option=option,
            range_filter=range)
        product_ids = qs.values_list("id", flat=True)
        # Stale-index guard: indeksas gali atsilikti nuo DB — jei ORM turi
        # prekių, bet counts tušti, facetai eitų tušti (klaidingai).
        # Tada fallback į ORM skaičiavimą.
        if not any(engine_counts.get(f) for f in (
                "brand_id", "category_ids", "group_id", "feature_facets")
        ) and qs.exists():
            engine_counts = None
    else:
        visibility = _visibility_for_channel(channel)
        _offer_price_expr, offer_filter = offer_price_exprs(visibility)

        qs = Product.objects.filter(is_active=True).annotate(
            _has_offer=Count("id", filter=offer_filter)
        )
        qs = apply_shop_product_scope(qs=qs, shop_id=shop_id)
        if channel == "outlet":
            qs = qs.filter(_has_offer__gt=0)

        qs, q, _engine_ordered = _apply_search_engine(
            qs, q=q, shop_id=shop_id, channel=channel, sort=None,
            range_filter=range)

        qs, selected_category = apply_catalog_filters(
            qs=qs,
            q=q,
            category_slug=category_slug,
            brand_slug=brand_slug,
            language_code=language_code,
            group_code=group_code,
            feature=feature,
            option=option,
            range_filter=range,
        )
        # Materializuojama vieną kartą: kitaip filtruotas subquery (su
        # Count anotacija) perskaičiuojamas kiekvienoje facet užklausoje.
        product_ids = list(qs.values_list("id", flat=True))

    payload = build_facets_payload(
        product_ids=product_ids,
        selected_category=selected_category,
        language_code=language_code,
        engine_counts=engine_counts,
    )
    cache.set(
        cache_key, payload,
        int(getattr(dj_settings, "FACETS_CACHE_SECONDS", 600)),
    )
    return payload


@router.get("/categories/{slug}/products", response=ProductPageOut)
def category_products(
    request,
    slug: str,
    country_code: str = "LT",
    channel: str = "normal",
    language_code: str | None = None,
    q: str | None = None,
    brand_slug: str | None = None,
    group_code: str | None = None,
    feature: str | None = None,
    option: str | None = None,
    range: str | None = None,
    sort: str | None = None,
    in_stock_only: bool = False,
    page: int = 1,
    page_size: int = 20,
):
    return products(
        request,
        country_code=country_code,
        channel=channel,
        language_code=language_code,
        q=q,
        category_slug=slug,
        brand_slug=brand_slug,
        group_code=group_code,
        feature=feature,
        option=option,
        range=range,
        sort=sort,
        in_stock_only=in_stock_only,
        page=page,
        page_size=page_size,
    )


@router.get("/brands/{slug}/products", response=ProductPageOut)
def brand_products(
    request,
    slug: str,
    country_code: str = "LT",
    channel: str = "normal",
    language_code: str | None = None,
    q: str | None = None,
    category_slug: str | None = None,
    group_code: str | None = None,
    feature: str | None = None,
    option: str | None = None,
    range: str | None = None,
    sort: str | None = None,
    in_stock_only: bool = False,
    page: int = 1,
    page_size: int = 20,
):
    return products(
        request,
        country_code=country_code,
        channel=channel,
        language_code=language_code,
        q=q,
        category_slug=category_slug,
        brand_slug=slug,
        group_code=group_code,
        feature=feature,
        option=option,
        range=range,
        sort=sort,
        in_stock_only=in_stock_only,
        page=page,
        page_size=page_size,
    )


@router.get("/product-groups/{code}/products", response=ProductPageOut)
def product_group_products(
    request,
    code: str,
    country_code: str = "LT",
    channel: str = "normal",
    language_code: str | None = None,
    q: str | None = None,
    category_slug: str | None = None,
    brand_slug: str | None = None,
    feature: str | None = None,
    option: str | None = None,
    range: str | None = None,
    sort: str | None = None,
    in_stock_only: bool = False,
    page: int = 1,
    page_size: int = 20,
):
    return products(
        request,
        country_code=country_code,
        channel=channel,
        language_code=language_code,
        q=q,
        category_slug=category_slug,
        brand_slug=brand_slug,
        group_code=code,
        feature=feature,
        option=option,
        range=range,
        sort=sort,
        in_stock_only=in_stock_only,
        page=page,
        page_size=page_size,
    )


@router.get("/products/{slug}", response=ProductDetailOut)
def product_detail(
    request,
    slug: str,
    country_code: str = "LT",
    channel: str = "normal",
    language_code: str | None = None,
):
    country_code, channel = _validate_country_and_channel(
        country_code=country_code, channel=channel
    )
    if language_code is None:
        language_code = get_request_language_code(request)

    shop = _shop_or_404_when_strict(request)
    products_qs = apply_shop_product_scope(
        qs=Product.objects.filter(is_active=True),
        shop_id=(int(shop.id) if shop else None),
    )
    resolved = resolve_product_by_slug(slug=slug, language_code=language_code, is_active=True)
    if resolved is None:
        raise HttpError(404, "Product not found")
    product = (
        products_qs.filter(id=resolved.id)
        .select_related("brand", "category", "tax_class")
        .prefetch_related(
            "images",
            "videos",
            "translations",
            "brand__translations",
            "category__translations",
            "feature_values__feature__translations",
            "feature_values__feature_value__translations",
            "feature_values__feature",
            "feature_values__feature_value",
            "variants",
            "variants__option_values__option_type",
            "variants__option_values__option_type__translations",
            "variants__option_values__option_value",
            "variants__option_values__option_value__translations",
            "variants__inventory_items",
        )
        .first()
    )
    if not product:
        raise HttpError(404, "Product not found")

    try:
        track_event(
            request=request,
            name="product_view",
            object_type="product",
            object_id=int(product.id),
            payload={"slug": product.slug, "sku": product.sku},
            country_code=country_code,
            channel=channel,
            language_code=(language_code or ""),
            shop_id=(int(shop.id) if shop else None),
        )
    except Exception:
        pass

    return build_product_detail_payload(
        product=product,
        shop_id=(int(shop.id) if shop else None),
        country_code=country_code,
        channel=channel,
        language_code=language_code,
    )
