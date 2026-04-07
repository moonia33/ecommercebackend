from __future__ import annotations

from decimal import Decimal

from django.conf import settings
from django.db.models import Case, Count, DecimalField, ExpressionWrapper, F, IntegerField, Min, Q, Sum, Value, When
from django.db.models.functions import Coalesce
from django.core.exceptions import ValidationError
from django.core.cache import cache
from django.db.models import Prefetch

from django.contrib.auth import get_user_model
from django.core.validators import validate_email
from django.utils import timezone
from ninja import Router
from ninja.errors import HttpError
from ninja.pagination import PageNumberPagination, paginate

from api.i18n import (
    get_request_country_code,
    get_request_language_code,
    normalize_country_code,
    translation_fallback_chain,
)
from pricing.services import compute_vat, get_vat_rate
from shipping.services import estimate_delivery_window

from analytics.services import track_event
from analytics.models import FavoriteProduct, RecentlyViewedProduct

from search.meili import MeiliError
from search.provider import search_products_facets, search_products_ids

from .content_blocks import get_content_blocks_for_product
from .api_schemas import (
    BackInStockSubscribeIn,
    BackInStockSubscribeOut,
    BrandOut,
    BrandRefOut,
    CatalogFacetsOut,
    CategoryOut,
    CategoryDetailOut,
    CategoryRefOut,
    ContentBlockOut,
    FeatureOut,
    MoneyOut,
    OptionTypeOut,
    ProductDetailOut,
    ProductGroupOut,
    ProductImageOut,
    ProductListOut,
    UiConfigOut,
    VariantOptionOut,
    VariantOut,
)
from .models import (
    BackInStockSubscription,
    Brand,
    Category,
    Feature,
    FeatureValue,
    InventoryItem,
    OptionType,
    OptionValue,
    Product,
    ProductFeatureValue,
    ProductGroup,
    ProductOptionType,
    SiteBrandExclusion,
    SiteCategoryBrandExclusion,
    SiteCategoryVisibility,
    Variant,
    VariantOptionValue,
)
from promotions.services import apply_promo_to_unit_net

router = Router(tags=["catalog"])


User = get_user_model()


def _get_request_site_id(request) -> int | None:
    site = getattr(request, "site", None)
    if site is None:
        return None
    sid = getattr(site, "id", None)
    if sid is None:
        return None
    try:
        sid_i = int(sid)
    except Exception:
        return None
    return sid_i if sid_i > 0 else None


@router.get("/ui", response=UiConfigOut)
def ui_config(request, language_code: str | None = None):
    from .content_blocks import get_content_blocks_by_keys

    if language_code is None:
        language_code = get_request_language_code(request)

    site_id = _get_request_site_id(request)
    blocks = get_content_blocks_by_keys(
        site_id=site_id,
        keys=["header", "menu", "footer"],
        language_code=language_code,
    )

    return UiConfigOut(
        blocks=[
            ContentBlockOut(
                key=b.key,
                title=b.title,
                placement=b.placement,
                type=b.type,
                payload=b.payload,
            )
            for b in blocks
        ]
    )


def _descendant_ids_map() -> dict[int, list[int]]:
    rows = Category.objects.filter(is_active=True).values("id", "parent_id")
    children: dict[int, list[int]] = {}
    for r in rows:
        pid = r["parent_id"]
        if pid is None:
            continue
        children.setdefault(int(pid), []).append(int(r["id"]))
    return children


def _descendant_ids_for_root(*, root_id: int, children: dict[int, list[int]] | None = None) -> list[int]:
    if children is None:
        children = _descendant_ids_map()

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


def _get_language_code(request) -> str:
    return get_request_language_code(request, query_param="language_code")


def _pick_best_translation(*, translations, fallback_langs: list[str]):
    order_index = {lang: i for i, lang in enumerate(fallback_langs)}
    best = None
    best_idx = 10_000
    for t in translations:
        idx = order_index.get((getattr(t, "language_code", "") or "").lower(), 10_000)
        if idx < best_idx:
            best = t
            best_idx = idx
    return best


def _translations_of(instance, cls):
    try:
        rel = getattr(instance, "translations", None)
        if rel is None:
            return []
        return [t for t in rel.all() if isinstance(t, cls)]
    except Exception:
        return []


def _resolve_category_by_slug(request, slug: str):
    from .models import CategoryTranslation

    language_code = _get_language_code(request)
    fallback_langs = translation_fallback_chain(language_code)

    t = (
        CategoryTranslation.objects.filter(language_code__in=fallback_langs, slug=str(slug))
        .select_related("category")
        .first()
    )
    if t is not None:
        return t.category, t

    c = Category.objects.filter(slug=str(slug), is_active=True).first()
    return c, None


def _resolve_brand_by_slug(request, slug: str):
    from .models import BrandTranslation

    language_code = _get_language_code(request)
    fallback_langs = translation_fallback_chain(language_code)

    t = (
        BrandTranslation.objects.filter(language_code__in=fallback_langs, slug=str(slug))
        .select_related("brand")
        .first()
    )
    if t is not None:
        return t.brand, t

    b = Brand.objects.filter(slug=str(slug), is_active=True).first()
    return b, None


def _resolve_user_and_visitor_id(request):
    user = None
    u = getattr(request, "user", None)
    if u is not None and getattr(u, "is_authenticated", False):
        user = u
    a = getattr(request, "auth", None)
    if user is None and isinstance(a, User):
        user = a
    if user is None and a is not None and getattr(a, "is_authenticated", False):
        user = a
    if user is None:
        try:
            from accounts.jwt_utils import decode_token

            cookie_name = getattr(settings, "AUTH_COOKIE_ACCESS_NAME", "access_token")
            token = (request.COOKIES.get(cookie_name) or "").strip()
            if token:
                payload = decode_token(token)
                if payload.get("type") == "access" and payload.get("sub"):
                    user = User.objects.get(id=int(payload["sub"]), is_active=True)
        except Exception:
            user = None

    try:
        visitor_id = (request.COOKIES.get("vid") or "").strip()
    except Exception:
        visitor_id = ""

    return user, visitor_id


class FavoritesPagination(PageNumberPagination):
    page_size = 24
    max_page_size = 24


@router.get("/favorites/ids", response=list[int])
def favorite_ids(request):
    site_id = _get_request_site_id(request)
    if site_id is None:
        return []

    user, visitor_id = _resolve_user_and_visitor_id(request)
    qs = FavoriteProduct.objects.filter(site_id=int(site_id))
    if user is not None:
        qs = qs.filter(user=user)
    elif visitor_id:
        qs = qs.filter(user__isnull=True, visitor_id=str(visitor_id))
    else:
        return []

    ids = list(qs.order_by("-created_at").values_list("product_id", flat=True)[:96])
    return [int(i) for i in ids]


@router.get("/favorites", response=list[ProductListOut])
@paginate(FavoritesPagination)
def favorites(
    request,
    country_code: str | None = None,
    channel: str = "normal",
):
    country_code = normalize_country_code(country_code) or get_request_country_code(request)

    channel = (channel or "normal").strip().lower()
    if channel not in {"normal", "outlet"}:
        raise HttpError(400, "Invalid channel")

    site_id = _get_request_site_id(request)
    if site_id is None:
        return []

    user, visitor_id = _resolve_user_and_visitor_id(request)
    fav_qs = FavoriteProduct.objects.filter(site_id=int(site_id))
    if user is not None:
        fav_qs = fav_qs.filter(user=user)
    elif visitor_id:
        fav_qs = fav_qs.filter(user__isnull=True, visitor_id=str(visitor_id))
    else:
        return []

    ids = list(fav_qs.order_by("-created_at").values_list("product_id", flat=True)[:96])
    if not ids:
        return []

    visibility = (
        InventoryItem.OfferVisibility.OUTLET
        if channel == "outlet"
        else InventoryItem.OfferVisibility.NORMAL
    )

    min_price_expr = Min("variants__price_eur", filter=Q(variants__is_active=True))
    offer_price_expr = Case(
        When(
            variants__inventory_items__offer_price_override_eur__isnull=False,
            then=F("variants__inventory_items__offer_price_override_eur"),
        ),
        When(
            variants__inventory_items__offer_discount_percent__isnull=False,
            then=ExpressionWrapper(
                F("variants__price_eur")
                * (Value(100) - F("variants__inventory_items__offer_discount_percent"))
                / Value(100),
                output_field=DecimalField(max_digits=12, decimal_places=2),
            ),
        ),
        default=F("variants__price_eur"),
        output_field=DecimalField(max_digits=12, decimal_places=2),
    )
    offer_filter = (
        Q(variants__is_active=True)
        & Q(variants__inventory_items__offer_visibility=visibility)
        & Q(variants__inventory_items__qty_on_hand__gt=F("variants__inventory_items__qty_reserved"))
    )
    min_offer_price_expr = Min(offer_price_expr, filter=offer_filter)

    products_qs = (
        Product.objects.filter(is_active=True, id__in=ids)
        .select_related("brand", "category", "tax_class")
        .prefetch_related("images")
        .annotate(_min_variant_price=min_price_expr)
        .annotate(_min_offer_price=min_offer_price_expr)
    )
    products_qs = _apply_site_assortment_to_product_qs(qs=products_qs, site_id=site_id, selected_category_id=None)

    product_by_id = {int(p.id): p for p in products_qs}
    ordered = [product_by_id.get(int(pid)) for pid in ids]
    ordered = [p for p in ordered if p is not None]

    vat_cache: dict[int, Decimal] = {}

    def vat_rate_for(product: Product) -> Decimal:
        if not product.tax_class_id:
            raise HttpError(400, "Product has no tax_class assigned")
        key = int(product.tax_class_id)
        if key in vat_cache:
            return vat_cache[key]
        try:
            rate = get_vat_rate(country_code=country_code, tax_class=product.tax_class)
        except LookupError:
            raise HttpError(400, "VAT rate not configured for country/tax_class")
        vat_cache[key] = Decimal(rate)
        return vat_cache[key]

    out: list[ProductListOut] = []
    for p in ordered:
        list_net = Decimal(p._min_variant_price if getattr(p, "_min_variant_price", None) is not None else 0)
        if getattr(p, "_min_offer_price", None) is not None:
            base_net = Decimal(p._min_offer_price)
        else:
            base_net = Decimal(list_net)
        rate = vat_rate_for(p)

        is_discounted_offer = bool(base_net and list_net and base_net < list_net)
        allow_additional_promotions = not is_discounted_offer

        sale_net, _rule = apply_promo_to_unit_net(
            base_unit_net=base_net,
            site_id=int(site_id),
            channel=channel,
            category_id=p.category_id,
            brand_id=p.brand_id,
            product_id=p.id,
            variant_id=None,
            customer_group_id=None,
            allow_additional_promotions=allow_additional_promotions,
            is_discounted_offer=is_discounted_offer,
        )

        compare_at_price = None
        discount_percent = _discount_percent(list_unit_net=list_net, sale_unit_net=sale_net)
        if discount_percent is not None:
            compare_at_price = _money_out(currency="EUR", unit_net=list_net, vat_rate=rate)

        imgs = list(p.images.all())
        imgs.sort(key=lambda i: (i.sort_order, i.id))
        images_out = []
        for img in imgs:
            if not img.url:
                continue
            list_avif = img.listing_avif_url or None
            list_webp = img.listing_webp_url or None
            list_url = list_avif or list_webp or img.url
            images_out.append(
                {
                    "avif_url": list_avif or (img.avif_url or None),
                    "webp_url": list_webp or (img.webp_url or None),
                    "url": list_url,
                    "alt_text": img.alt_text,
                    "sort_order": img.sort_order,
                }
            )
            if len(images_out) >= 2:
                break

        out.append(
            {
                "id": p.id,
                "sku": p.sku,
                "slug": p.slug,
                "name": p.name,
                "is_active": bool(p.is_active),
                "brand": {
                    "id": p.brand.id,
                    "slug": p.brand.slug,
                    "name": p.brand.name,
                }
                if p.brand
                else None,
                "category": {
                    "id": p.category.id,
                    "slug": p.category.slug,
                    "name": p.category.name,
                }
                if p.category
                else None,
                "images": images_out,
                "price": _money_out(currency="EUR", unit_net=Decimal(sale_net), vat_rate=rate),
                "compare_at_price": compare_at_price,
                "discount_percent": discount_percent,
            }
        )

    return out


@router.post("/favorites/{product_id}")
def favorite_add(request, product_id: int):
    site_id = _get_request_site_id(request)
    if site_id is None:
        raise HttpError(400, "Site is not resolved")

    user, visitor_id = _resolve_user_and_visitor_id(request)
    if user is None and not visitor_id:
        raise HttpError(400, "Missing visitor id")

    if not Product.objects.filter(id=int(product_id), is_active=True).exists():
        raise HttpError(404, "Product not found")

    if user is not None:
        FavoriteProduct.objects.get_or_create(site_id=int(site_id), user=user, product_id=int(product_id), defaults={"visitor_id": ""})
        ids_to_keep = list(
            FavoriteProduct.objects.filter(site_id=int(site_id), user=user)
            .order_by("-created_at")
            .values_list("id", flat=True)[:96]
        )
        FavoriteProduct.objects.filter(site_id=int(site_id), user=user).exclude(id__in=ids_to_keep).delete()
    else:
        FavoriteProduct.objects.get_or_create(
            site_id=int(site_id),
            user=None,
            visitor_id=str(visitor_id),
            product_id=int(product_id),
            defaults={},
        )
        ids_to_keep = list(
            FavoriteProduct.objects.filter(site_id=int(site_id), user__isnull=True, visitor_id=str(visitor_id))
            .order_by("-created_at")
            .values_list("id", flat=True)[:96]
        )
        FavoriteProduct.objects.filter(site_id=int(site_id), user__isnull=True, visitor_id=str(visitor_id)).exclude(
            id__in=ids_to_keep
        ).delete()

    return {"status": "ok"}


@router.delete("/favorites/{product_id}")
def favorite_remove(request, product_id: int):
    site_id = _get_request_site_id(request)
    if site_id is None:
        raise HttpError(400, "Site is not resolved")

    user, visitor_id = _resolve_user_and_visitor_id(request)
    qs = FavoriteProduct.objects.filter(site_id=int(site_id), product_id=int(product_id))
    if user is not None:
        qs = qs.filter(user=user)
    elif visitor_id:
        qs = qs.filter(user__isnull=True, visitor_id=str(visitor_id))
    else:
        return {"status": "ok"}

    qs.delete()
    return {"status": "ok"}


def _ancestor_ids_for_ids(*, ids: set[int]) -> set[int]:
    if not ids:
        return set()
    rows = Category.objects.filter(is_active=True).values("id", "parent_id")
    parent_of: dict[int, int | None] = {int(r["id"]): (int(r["parent_id"]) if r["parent_id"] is not None else None) for r in rows}
    out = set(int(i) for i in ids)
    stack = list(out)
    while stack:
        cid = stack.pop()
        pid = parent_of.get(int(cid))
        if pid is None:
            continue
        if pid in out:
            continue
        out.add(pid)
        stack.append(pid)
    return out


def _get_site_allowed_category_ids(*, site_id: int) -> set[int] | None:
    rules = list(
        SiteCategoryVisibility.objects.filter(site_id=int(site_id), is_active=True)
        .only("category_id", "include_descendants")
    )
    if not rules:
        return None

    children = _descendant_ids_map()
    allowed: set[int] = set()
    for r in rules:
        cid = int(r.category_id)
        if bool(r.include_descendants):
            allowed.update(_descendant_ids_for_root(root_id=cid, children=children))
        else:
            allowed.add(cid)
    return allowed


def _get_site_excluded_brand_ids(*, site_id: int) -> set[int]:
    return set(
        int(i)
        for i in SiteBrandExclusion.objects.filter(site_id=int(site_id), is_active=True).values_list(
            "brand_id", flat=True
        )
    )


def _get_site_category_excluded_brand_ids(*, site_id: int, category_id: int) -> set[int]:
    rules = list(
        SiteCategoryBrandExclusion.objects.filter(site_id=int(site_id), is_active=True)
        .only("category_id", "include_descendants", "brand_id")
    )
    if not rules:
        return set()

    children = _descendant_ids_map()
    out: set[int] = set()
    for r in rules:
        root_id = int(r.category_id)
        if bool(r.include_descendants):
            ids = _descendant_ids_for_root(root_id=root_id, children=children)
            if int(category_id) in set(ids):
                out.add(int(r.brand_id))
        else:
            if int(category_id) == root_id:
                out.add(int(r.brand_id))
    return out


def _apply_site_assortment_to_product_qs(
    *,
    qs,
    site_id: int | None,
    selected_category_id: int | None,
):
    if site_id is None:
        return qs

    allowed = _get_site_allowed_category_ids(site_id=site_id)
    if allowed is not None:
        qs = qs.filter(category_id__in=list(allowed))

    excluded = _get_site_excluded_brand_ids(site_id=site_id)
    if selected_category_id is not None:
        excluded |= _get_site_category_excluded_brand_ids(
            site_id=site_id, category_id=int(selected_category_id)
        )
    if excluded:
        qs = qs.exclude(brand_id__in=list(excluded))

    return qs


def _money_out(*, currency: str, unit_net: Decimal, vat_rate: Decimal) -> MoneyOut:
    b = compute_vat(unit_net=Decimal(unit_net),
                    vat_rate=Decimal(vat_rate), qty=1)
    return {
        "currency": currency,
        "net": b.unit_net,
        "vat_rate": b.vat_rate,
        "vat": b.unit_vat,
        "gross": b.unit_gross,
    }


def _effective_offer_unit_net(*, list_unit_net: Decimal, offer: InventoryItem) -> Decimal:
    if bool(getattr(offer, "never_discount", False)):
        return Decimal(list_unit_net)
    if offer.offer_price_override_eur is not None:
        return Decimal(offer.offer_price_override_eur)
    if offer.offer_discount_percent is not None:
        pct = int(offer.offer_discount_percent)
        pct = max(0, min(100, pct))
        return (Decimal(list_unit_net) * (Decimal(100 - pct) / Decimal(100))).quantize(Decimal("0.01"))
    return Decimal(list_unit_net)


def _discount_percent(*, list_unit_net: Decimal, sale_unit_net: Decimal) -> int | None:
    list_unit_net = Decimal(list_unit_net)
    sale_unit_net = Decimal(sale_unit_net)
    if list_unit_net <= 0:
        return None
    if sale_unit_net >= list_unit_net:
        return None
    pct = int(((list_unit_net - sale_unit_net) / list_unit_net * Decimal(100)).quantize(Decimal("1")))
    return max(0, min(100, pct))


class ProductPagination(PageNumberPagination):
    page_size = 20
    max_page_size = 100


def _parse_pairs(value: str | None) -> list[tuple[str, str]]:
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


@router.get("/recently-viewed", response=list[ProductListOut])
def recently_viewed(
    request,
    country_code: str | None = None,
    channel: str = "normal",
    limit: int | None = None,
):
    country_code = normalize_country_code(country_code) or get_request_country_code(request)

    channel = (channel or "normal").strip().lower()
    if channel not in {"normal", "outlet"}:
        raise HttpError(400, "Invalid channel")

    site_id = _get_request_site_id(request)

    try:
        default_limit = int(getattr(settings, "RECENTLY_VIEWED_MAX", 12))
    except Exception:
        default_limit = 12
    limit_v = int(limit) if limit is not None else default_limit
    limit_v = max(1, min(100, limit_v))

    user = None
    u = getattr(request, "user", None)
    if u is not None and getattr(u, "is_authenticated", False):
        user = u
    a = getattr(request, "auth", None)
    if user is None and isinstance(a, User):
        user = a
    if user is None and a is not None and getattr(a, "is_authenticated", False):
        user = a
    if user is None:
        try:
            from accounts.jwt_utils import decode_token

            cookie_name = getattr(settings, "AUTH_COOKIE_ACCESS_NAME", "access_token")
            token = (request.COOKIES.get(cookie_name) or "").strip()
            if token:
                payload = decode_token(token)
                if payload.get("type") == "access" and payload.get("sub"):
                    user = User.objects.get(id=int(payload["sub"]), is_active=True)
        except Exception:
            user = None

    try:
        visitor_id = (request.COOKIES.get("vid") or "").strip()
    except Exception:
        visitor_id = ""

    qs = RecentlyViewedProduct.objects.all()
    site_id = _get_request_site_id(request)
    if site_id is None:
        return []
    qs = qs.filter(site_id=int(site_id))
    if user is not None:
        qs = qs.filter(user=user)
    elif visitor_id:
        qs = qs.filter(user__isnull=True, visitor_id=visitor_id)
    else:
        return []

    ids = list(qs.order_by("-last_viewed_at").values_list("product_id", flat=True)[:limit_v])
    if not ids:
        return []

    visibility = (
        InventoryItem.OfferVisibility.OUTLET
        if channel == "outlet"
        else InventoryItem.OfferVisibility.NORMAL
    )

    # site_id already resolved above
    selected_category: Category | None = None
    selected_category_ids: list[int] | None = None

    qs = Product.objects.filter(is_active=True).annotate(
        _min_variant_price=Min("variants__price_eur"),
        _min_offer_price=Min(
            "variants__inventory_items__offer_price_override_eur",
            filter=Q(variants__is_active=True)
            & Q(variants__inventory_items__offer_visibility=visibility)
            & Q(variants__inventory_items__qty_on_hand__gt=F("variants__inventory_items__qty_reserved")),
        ),
    )

    qs = _apply_site_assortment_to_product_qs(qs=qs, site_id=site_id, selected_category_id=None)

    min_price_expr = Min("variants__price_eur", filter=Q(variants__is_active=True))
    offer_price_expr = Case(
        When(
            variants__inventory_items__offer_price_override_eur__isnull=False,
            then=F("variants__inventory_items__offer_price_override_eur"),
        ),
        When(
            variants__inventory_items__offer_discount_percent__isnull=False,
            then=ExpressionWrapper(
                F("variants__price_eur")
                * (Value(100) - F("variants__inventory_items__offer_discount_percent"))
                / Value(100),
                output_field=DecimalField(max_digits=12, decimal_places=2),
            ),
        ),
        default=F("variants__price_eur"),
        output_field=DecimalField(max_digits=12, decimal_places=2),
    )

    offer_filter = (
        Q(variants__is_active=True)
        & Q(variants__inventory_items__offer_visibility=visibility)
        & Q(variants__inventory_items__qty_on_hand__gt=F("variants__inventory_items__qty_reserved"))
    )
    min_offer_price_expr = Min(offer_price_expr, filter=offer_filter)

    products_qs = (
        Product.objects.filter(is_active=True, id__in=ids)
        .select_related("brand", "category", "tax_class")
        .prefetch_related("images")
        .annotate(_min_variant_price=min_price_expr)
        .annotate(_min_offer_price=min_offer_price_expr)
    )

    product_by_id = {int(p.id): p for p in products_qs}
    ordered = [product_by_id.get(int(pid)) for pid in ids]
    ordered = [p for p in ordered if p is not None]

    vat_cache: dict[int, Decimal] = {}

    def vat_rate_for(product: Product) -> Decimal:
        if not product.tax_class_id:
            raise HttpError(400, "Product has no tax_class assigned")
        key = int(product.tax_class_id)
        if key in vat_cache:
            return vat_cache[key]
        try:
            rate = get_vat_rate(country_code=country_code, tax_class=product.tax_class)
        except LookupError:
            raise HttpError(400, "VAT rate not configured for country/tax_class")
        vat_cache[key] = Decimal(rate)
        return vat_cache[key]

    out: list[ProductListOut] = []
    for p in ordered:
        list_net = Decimal(p._min_variant_price if getattr(p, "_min_variant_price", None) is not None else 0)
        if getattr(p, "_min_offer_price", None) is not None:
            base_net = Decimal(p._min_offer_price)
        else:
            base_net = Decimal(list_net)
        rate = vat_rate_for(p)

        is_discounted_offer = bool(base_net and list_net and base_net < list_net)
        allow_additional_promotions = not is_discounted_offer

        site_id = _get_request_site_id(request)
        if site_id is None:
            site_id = 0

        sale_net, _rule = apply_promo_to_unit_net(
            base_unit_net=base_net,
            site_id=int(site_id),
            channel=channel,
            category_id=p.category_id,
            brand_id=p.brand_id,
            product_id=p.id,
            variant_id=None,
            customer_group_id=None,
            allow_additional_promotions=allow_additional_promotions,
            is_discounted_offer=is_discounted_offer,
        )

        compare_at_price = None
        discount_percent = _discount_percent(list_unit_net=list_net, sale_unit_net=sale_net)
        if discount_percent is not None:
            compare_at_price = _money_out(currency="EUR", unit_net=list_net, vat_rate=rate)

        imgs = list(p.images.all())
        imgs.sort(key=lambda i: (i.sort_order, i.id))
        images_out = []
        for img in imgs:
            if not img.url:
                continue
            list_avif = img.listing_avif_url or None
            list_webp = img.listing_webp_url or None
            list_url = list_avif or list_webp or img.url
            images_out.append(
                {
                    "avif_url": list_avif or (img.avif_url or None),
                    "webp_url": list_webp or (img.webp_url or None),
                    "url": list_url,
                    "alt_text": img.alt_text,
                    "sort_order": img.sort_order,
                }
            )
            if len(images_out) >= 2:
                break

        out.append(
            {
                "id": p.id,
                "sku": p.sku,
                "slug": p.slug,
                "name": p.name,
                "is_active": bool(p.is_active),
                "brand": {
                    "id": p.brand.id,
                    "slug": p.brand.slug,
                    "name": p.brand.name,
                }
                if p.brand
                else None,
                "category": {
                    "id": p.category.id,
                    "slug": p.category.slug,
                    "name": p.category.name,
                }
                if p.category
                else None,
                "images": images_out,
                "price": _money_out(currency="EUR", unit_net=Decimal(sale_net), vat_rate=rate),
                "compare_at_price": compare_at_price,
                "discount_percent": discount_percent,
            }
        )

    return out


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


@router.get("/categories", response=list[CategoryOut])
def categories(request):
    site_id = _get_request_site_id(request)

    language_code = _get_language_code(request)
    fallback_langs = translation_fallback_chain(language_code)

    allowed = None
    if site_id is not None:
        allowed = _get_site_allowed_category_ids(site_id=site_id)

    qs = Category.objects.filter(is_active=True)
    if allowed is not None:
        allowed_with_ancestors = _ancestor_ids_for_ids(ids=set(allowed))
        qs = qs.filter(id__in=list(allowed_with_ancestors))
    qs = qs.order_by("tree_id", "lft")

    categories_list = list(qs)
    ids = [int(c.id) for c in categories_list]
    from .models import CategoryTranslation

    translations = list(
        CategoryTranslation.objects.filter(
            category_id__in=ids,
            language_code__in=fallback_langs,
        ).only(
            "category_id",
            "language_code",
            "name",
            "slug",
            "description",
            "seo_title",
            "seo_description",
            "seo_keywords",
        )
    )
    by_category: dict[int, list] = {}
    for t in translations:
        by_category.setdefault(int(t.category_id), []).append(t)

    out = []
    for c in categories_list:
        best = _pick_best_translation(translations=by_category.get(int(c.id), []), fallback_langs=fallback_langs)
        out.append(
            {
                "id": c.id,
                "slug": (getattr(best, "slug", "") or c.slug),
                "name": (getattr(best, "name", "") or c.name),
                "parent_id": c.parent_id,
                "description": (getattr(best, "description", "") or c.description or ""),
                "hero_image_url": (
                    (c.hero_image.url if getattr(c, "hero_image", None) else "")
                    or (getattr(c, "hero_image_url", "") or "")
                    or None
                ),
                "menu_icon_url": (
                    (c.menu_icon.url if getattr(c, "menu_icon", None) else "")
                    or (getattr(c, "menu_icon_url", "") or "")
                    or None
                ),
                "seo_title": (getattr(best, "seo_title", "") or getattr(c, "seo_title", "") or ""),
                "seo_description": (
                    getattr(best, "seo_description", "") or getattr(c, "seo_description", "") or ""
                ),
                "seo_keywords": (getattr(best, "seo_keywords", "") or getattr(c, "seo_keywords", "") or ""),
            }
        )
    return out


@router.get("/categories/{slug}", response=CategoryDetailOut)
def category_detail(request, slug: str):
    site_id = _get_request_site_id(request)
    allowed = None
    if site_id is not None:
        allowed = _get_site_allowed_category_ids(site_id=site_id)

    c, t = _resolve_category_by_slug(request, slug)
    if not c:
        raise HttpError(404, "Category not found")

    c = Category.objects.filter(id=int(c.id), is_active=True).select_related("parent").first()
    if not c:
        raise HttpError(404, "Category not found")

    if allowed is not None and int(c.id) not in set(allowed):
        raise HttpError(404, "Category not found")
    return {
        "id": c.id,
        "slug": (getattr(t, "slug", "") or c.slug),
        "name": (getattr(t, "name", "") or c.name),
        "parent_id": c.parent_id,
        "description": (getattr(t, "description", "") or c.description or ""),
        "hero_image_url": (
            (c.hero_image.url if getattr(c, "hero_image", None) else "")
            or (getattr(c, "hero_image_url", "") or "")
            or None
        ),
        "menu_icon_url": (
            (c.menu_icon.url if getattr(c, "menu_icon", None) else "")
            or (getattr(c, "menu_icon_url", "") or "")
            or None
        ),
        "seo_title": (getattr(t, "seo_title", "") or getattr(c, "seo_title", "") or ""),
        "seo_description": (getattr(t, "seo_description", "") or getattr(c, "seo_description", "") or ""),
        "seo_keywords": (getattr(t, "seo_keywords", "") or getattr(c, "seo_keywords", "") or ""),
    }


@router.get("/brands", response=list[BrandOut])
def brands(request):
    site_id = _get_request_site_id(request)
    allowed = None
    excluded = set()

    language_code = _get_language_code(request)
    fallback_langs = translation_fallback_chain(language_code)
    if site_id is not None:
        allowed = _get_site_allowed_category_ids(site_id=site_id)
        excluded = _get_site_excluded_brand_ids(site_id=site_id)

    qs = Brand.objects.filter(is_active=True)
    if excluded:
        qs = qs.exclude(id__in=list(excluded))
    if allowed is not None:
        qs = qs.filter(products__is_active=True, products__category_id__in=list(allowed)).distinct()

    qs = qs.order_by("name")
    brands_list = list(qs)
    ids = [int(b.id) for b in brands_list]
    from .models import BrandTranslation

    translations = list(
        BrandTranslation.objects.filter(
            brand_id__in=ids,
            language_code__in=fallback_langs,
        ).only(
            "brand_id",
            "language_code",
            "name",
            "slug",
            "description",
            "seo_title",
            "seo_description",
            "seo_keywords",
        )
    )
    by_brand: dict[int, list] = {}
    for t in translations:
        by_brand.setdefault(int(t.brand_id), []).append(t)

    out = []
    for b in brands_list:
        best = _pick_best_translation(translations=by_brand.get(int(b.id), []), fallback_langs=fallback_langs)
        out.append(
            {
                "id": b.id,
                "slug": (getattr(best, "slug", "") or b.slug),
                "name": (getattr(best, "name", "") or b.name),
                "description": (getattr(best, "description", "") or getattr(b, "description", "") or ""),
                "logo_url": (getattr(b, "logo_url_resolved", "") or None),
                "seo_title": (getattr(best, "seo_title", "") or getattr(b, "seo_title", "") or ""),
                "seo_description": (getattr(best, "seo_description", "") or getattr(b, "seo_description", "") or ""),
                "seo_keywords": (getattr(best, "seo_keywords", "") or getattr(b, "seo_keywords", "") or ""),
            }
        )
    return out


@router.get("/brands/{slug}", response=BrandOut)
def brand_detail(request, slug: str):
    site_id = _get_request_site_id(request)
    excluded = set()
    if site_id is not None:
        excluded = _get_site_excluded_brand_ids(site_id=site_id)

    b, t = _resolve_brand_by_slug(request, slug)
    if not b:
        raise HttpError(404, "Brand not found")

    b = Brand.objects.filter(id=int(b.id), is_active=True).first()
    if not b:
        raise HttpError(404, "Brand not found")

    if excluded and int(b.id) in excluded:
        raise HttpError(404, "Brand not found")
    return {
        "id": b.id,
        "slug": (getattr(t, "slug", "") or b.slug),
        "name": (getattr(t, "name", "") or b.name),
        "description": (getattr(t, "description", "") or getattr(b, "description", "") or ""),
        "logo_url": (getattr(b, "logo_url_resolved", "") or None),
        "seo_title": (getattr(t, "seo_title", "") or getattr(b, "seo_title", "") or ""),
        "seo_description": (getattr(t, "seo_description", "") or getattr(b, "seo_description", "") or ""),
        "seo_keywords": (getattr(t, "seo_keywords", "") or getattr(b, "seo_keywords", "") or ""),
    }


@router.get("/product-groups", response=list[ProductGroupOut])
def product_groups(request):
    from .models import ProductGroupTranslation

    language_code = _get_language_code(request)
    fallback_langs = translation_fallback_chain(language_code)

    qs = (
        ProductGroup.objects.filter(is_active=True)
        .prefetch_related("translations")
        .order_by("name")
    )
    out: list[ProductGroupOut] = []
    for g in qs:
        translations = [t for t in getattr(g, "translations", []).all() if isinstance(t, ProductGroupTranslation)]
        t = _pick_best_translation(translations=translations, fallback_langs=fallback_langs)
        out.append(
            {
                "id": g.id,
                "code": g.code,
                "slug": (getattr(t, "slug", "") or getattr(g, "slug", "") or ""),
                "name": (getattr(t, "name", "") or g.name),
                "description": (getattr(t, "description", "") or (g.description or "")),
            }
        )
    return out


@router.get("/product-groups/{code}", response=ProductGroupOut)
def product_group_detail(request, code: str):
    from .models import ProductGroupTranslation

    language_code = _get_language_code(request)
    fallback_langs = translation_fallback_chain(language_code)

    g = ProductGroup.objects.filter(code=code, is_active=True).prefetch_related("translations").first()
    if not g:
        raise HttpError(404, "Product group not found")
    translations = [t for t in getattr(g, "translations", []).all() if isinstance(t, ProductGroupTranslation)]
    t = _pick_best_translation(translations=translations, fallback_langs=fallback_langs)
    return {
        "id": g.id,
        "code": g.code,
        "slug": (getattr(t, "slug", "") or getattr(g, "slug", "") or ""),
        "name": (getattr(t, "name", "") or g.name),
        "description": (getattr(t, "description", "") or (g.description or "")),
    }


@router.get("/features", response=list[FeatureOut])
def features(request):
    from .models import FeatureTranslation, FeatureValueTranslation

    language_code = _get_language_code(request)
    fallback_langs = translation_fallback_chain(language_code)

    qs = (
        Feature.objects.filter(is_active=True, is_filterable=True)
        .prefetch_related("values", "translations", "values__translations")
        .order_by("sort_order", "code")
    )
    out: list[FeatureOut] = []
    for f in qs:
        f_translations = [t for t in getattr(f, "translations", []).all() if isinstance(t, FeatureTranslation)]
        ft = _pick_best_translation(translations=f_translations, fallback_langs=fallback_langs)

        vals = [v for v in f.values.all() if v.is_active]
        vals.sort(key=lambda v: (v.sort_order, v.value, v.id))
        values_out = []
        for v in vals:
            v_translations = [t for t in getattr(v, "translations", []).all() if isinstance(t, FeatureValueTranslation)]
            vt = _pick_best_translation(translations=v_translations, fallback_langs=fallback_langs)
            values_out.append({"id": v.id, "value": (getattr(vt, "value", "") or v.value)})
        out.append(
            {
                "id": f.id,
                "code": f.code,
                "name": (getattr(ft, "name", "") or f.name),
                "values": values_out,
            }
        )
    return out


@router.get("/option-types", response=list[OptionTypeOut])
def option_types(request):
    from .models import OptionTypeTranslation, OptionValueTranslation

    language_code = _get_language_code(request)
    fallback_langs = translation_fallback_chain(language_code)

    qs = (
        OptionType.objects.filter(is_active=True)
        .prefetch_related("values", "translations", "values__translations")
        .order_by("sort_order", "code")
    )
    out: list[OptionTypeOut] = []
    for t in qs:
        t_translations = [x for x in getattr(t, "translations", []).all() if isinstance(x, OptionTypeTranslation)]
        tt = _pick_best_translation(translations=t_translations, fallback_langs=fallback_langs)

        vals = [v for v in t.values.all() if v.is_active]
        vals.sort(key=lambda v: (v.sort_order, v.label, v.id))
        values_out = []
        for v in vals:
            v_translations = [x for x in getattr(v, "translations", []).all() if isinstance(x, OptionValueTranslation)]
            vt = _pick_best_translation(translations=v_translations, fallback_langs=fallback_langs)
            values_out.append({"id": v.id, "code": v.code, "label": (getattr(vt, "label", "") or v.label)})
        out.append(
            {
                "id": t.id,
                "code": t.code,
                "name": (getattr(tt, "name", "") or t.name),
                "display_type": t.display_type,
                "swatch_type": t.swatch_type,
                "values": values_out,
            }
        )
    return out


@router.get("/products", response=list[ProductListOut])
@paginate(ProductPagination)
def products(
    request,
    country_code: str | None = None,
    channel: str = "normal",
    q: str | None = None,
    category_slug: str | None = None,
    brand_slug: str | None = None,
    group_code: str | None = None,
    feature: str | None = None,
    option: str | None = None,
    sort: str | None = None,
    in_stock_only: bool = False,
):
    country_code = normalize_country_code(country_code) or get_request_country_code(request)

    channel = (channel or "normal").strip().lower()
    if channel not in {"normal", "outlet"}:
        raise HttpError(400, "Invalid channel")

    site_id = _get_request_site_id(request)
    selected_category: Category | None = None

    visibility = (
        InventoryItem.OfferVisibility.OUTLET
        if channel == "outlet"
        else InventoryItem.OfferVisibility.NORMAL
    )

    # Representative list price: min active variant price (net) per product.
    min_price_expr = Min("variants__price_eur", filter=Q(variants__is_active=True))

    # Representative offer price: min effective offer sale price (net) per product.
    # Only consider inventory items with available stock and matching visibility.
    offer_price_expr = Case(
        When(
            variants__inventory_items__never_discount=True,
            then=F("variants__price_eur"),
        ),
        When(
            variants__inventory_items__offer_price_override_eur__isnull=False,
            then=F("variants__inventory_items__offer_price_override_eur"),
        ),
        When(
            variants__inventory_items__offer_discount_percent__isnull=False,
            then=ExpressionWrapper(
                F("variants__price_eur")
                * (Value(100) - F("variants__inventory_items__offer_discount_percent"))
                / Value(100),
                output_field=DecimalField(max_digits=12, decimal_places=2),
            ),
        ),
        default=F("variants__price_eur"),
        output_field=DecimalField(max_digits=12, decimal_places=2),
    )

    offer_filter = (
        Q(variants__is_active=True)
        & Q(variants__inventory_items__offer_visibility=visibility)
        & Q(variants__inventory_items__qty_on_hand__gt=F("variants__inventory_items__qty_reserved"))
    )
    min_offer_price_expr = Min(offer_price_expr, filter=offer_filter)

    qs = (
        Product.objects.filter(is_active=True)
        .select_related("brand", "category", "tax_class")
        .prefetch_related("images")
        .annotate(_min_variant_price=min_price_expr)
        .annotate(_min_offer_price=min_offer_price_expr)
    )

    qs = qs.annotate(
        _has_stock=Case(
            When(_min_offer_price__isnull=False, then=Value(1)),
            default=Value(0),
            output_field=IntegerField(),
        )
    )

    if in_stock_only:
        qs = qs.filter(_has_stock=1)

    # Sorting
    sort_v = (sort or "").strip().lower()
    # NOTE: list price calculations and promo adjustments are applied in Python later.
    # Therefore, price-based sorting uses DB representative base price annotations.
    if sort_v in {"price", "-price"}:
        qs = qs.annotate(_sort_price=Coalesce("_min_offer_price", "_min_variant_price"))
        qs = (
            qs.order_by("-_has_stock", "_sort_price", "name", "id")
            if sort_v == "price"
            else qs.order_by("-_has_stock", "-_sort_price", "name", "id")
        )
    elif sort_v in {"created", "created_at", "-created", "-created_at"}:
        if sort_v.startswith("-"):
            qs = qs.order_by("-_has_stock", "-created_at", "-id")
        else:
            qs = qs.order_by("-_has_stock", "created_at", "id")
    elif sort_v in {"discounted", "-discounted"}:
        qs = qs.annotate(
            _is_discounted=Case(
                When(_min_offer_price__isnull=False, _min_offer_price__lt=F("_min_variant_price"), then=Value(1)),
                default=Value(0),
                output_field=IntegerField(),
            )
        )
        # If sort=discounted, show discounted first; if sort=-discounted, show non-discounted first.
        qs = (
            qs.order_by("-_has_stock", "-_is_discounted", "name", "id")
            if sort_v == "discounted"
            else qs.order_by("-_has_stock", "_is_discounted", "name", "id")
        )
    elif sort_v in {"best_selling", "-best_selling"}:
        from checkout.models import Order

        qs = qs.annotate(
            _sold_qty=Coalesce(
                Sum(
                    "variants__order_lines__qty",
                    filter=Q(variants__order_lines__order__status=Order.Status.PAID),
                    output_field=IntegerField(),
                ),
                Value(0),
            )
        )
        qs = (
            qs.order_by("-_has_stock", "-_sold_qty", "name", "id")
            if sort_v == "best_selling"
            else qs.order_by("-_has_stock", "_sold_qty", "name", "id")
        )
    else:
        qs = qs.order_by("-_has_stock", "name", "id")

    meili_ids: list[int] | None = None
    if q:
        qv = q.strip()
        if qv:
            try:
                index_uid = str(getattr(settings, "MEILI_PRODUCTS_INDEX", "products_lt_v1"))

                category_ancestor_ids = None
                if category_slug:
                    selected_category = Category.objects.filter(slug=category_slug, is_active=True).first()
                    if not selected_category:
                        raise HttpError(404, "Category not found")
                    category_ancestor_ids = list(_ancestor_ids_for_ids(ids={int(selected_category.id)}))

                b_id = None
                if brand_slug:
                    b = Brand.objects.filter(slug=brand_slug, is_active=True).first()
                    if not b:
                        raise HttpError(404, "Brand not found")
                    b_id = int(b.id)

                g_id = None
                if group_code:
                    g = ProductGroup.objects.filter(code=group_code, is_active=True).first()
                    if not g:
                        raise HttpError(404, "Product group not found")
                    g_id = int(g.id)

                fv_ids: list[int] = []
                for f_code, f_val in _parse_pairs(feature):
                    fv = (
                        FeatureValue.objects.filter(
                            feature__code=f_code,
                            value=f_val,
                            is_active=True,
                            feature__is_active=True,
                            feature__is_filterable=True,
                        )
                        .values_list("id", flat=True)
                        .first()
                    )
                    if fv:
                        fv_ids.append(int(fv))

                ov_ids: list[int] = []
                for o_type, o_val in _parse_pairs(option):
                    ov = (
                        OptionValue.objects.filter(
                            option_type__code=o_type,
                            code=o_val,
                            is_active=True,
                            option_type__is_active=True,
                        )
                        .values_list("id", flat=True)
                        .first()
                    )
                    if ov:
                        ov_ids.append(int(ov))

                res = search_products_ids(
                    q=qv,
                    index_uid=index_uid,
                    site_id=site_id,
                    channel=channel,
                    in_stock_only=bool(in_stock_only),
                    category_ancestor_ids=category_ancestor_ids,
                    brand_id=b_id,
                    group_id=g_id,
                    feature_value_ids=fv_ids or None,
                    option_value_ids=ov_ids or None,
                    offset=0,
                    limit=200,
                )
                if res.ids:
                    meili_ids = list(res.ids)
                    qs = qs.filter(id__in=meili_ids)
            except MeiliError:
                meili_ids = None

    if meili_ids is None:
        if q:
            qv = q.strip()
            if qv:
                qs = qs.filter(Q(name__icontains=qv) | Q(slug__icontains=qv) | Q(sku__icontains=qv))

    if category_slug:
        selected_category = Category.objects.filter(slug=category_slug, is_active=True).first()
        if not selected_category:
            raise HttpError(404, "Category not found")
        ids = _descendant_category_ids(root_id=int(selected_category.id))
        qs = qs.filter(category_id__in=ids)

    if brand_slug:
        b = Brand.objects.filter(slug=brand_slug, is_active=True).first()
        if not b:
            raise HttpError(404, "Brand not found")
        qs = qs.filter(brand_id=b.id)

    if group_code:
        g = ProductGroup.objects.filter(code=group_code, is_active=True).first()
        if not g:
            raise HttpError(404, "Product group not found")
        qs = qs.filter(group_id=g.id)

    for f_code, f_val in _parse_pairs(feature):
        qs = qs.filter(
            feature_values__feature__code=f_code,
            feature_values__feature_value__value=f_val,
        )

    for o_type, o_val in _parse_pairs(option):
        qs = qs.filter(
            variants__option_values__option_type__code=o_type,
            variants__option_values__option_value__code=o_val,
        )

    if feature or option:
        qs = qs.distinct()

    # Apply site assortment rules late (after user filters), but before channel/outlet filter.
    qs = _apply_site_assortment_to_product_qs(
        qs=qs,
        site_id=site_id,
        selected_category_id=(int(selected_category.id) if selected_category is not None else None),
    )

    if channel == "outlet":
        qs = qs.filter(_min_offer_price__isnull=False)

    vat_cache: dict[int, Decimal] = {}

    def vat_rate_for(product: Product) -> Decimal:
        if not product.tax_class_id:
            raise HttpError(400, "Product has no tax_class assigned")
        key = int(product.tax_class_id)
        if key in vat_cache:
            return vat_cache[key]
        try:
            rate = get_vat_rate(country_code=country_code,
                                tax_class=product.tax_class)
        except LookupError:
            raise HttpError(
                400, "VAT rate not configured for country/tax_class")
        vat_cache[key] = Decimal(rate)
        return vat_cache[key]

    out: list[ProductListOut] = []
    if meili_ids:
        by_id = {int(p.id): p for p in qs}
        ordered = [by_id.get(int(i)) for i in meili_ids if int(i) in by_id]
    else:
        ordered = list(qs)

    for p in ordered:
        if p is None:
            continue
        list_net = Decimal(p._min_variant_price if getattr(p, "_min_variant_price", None) is not None else 0)
        if getattr(p, "_min_offer_price", None) is not None:
            base_net = Decimal(p._min_offer_price)
        else:
            base_net = Decimal(list_net)
        rate = vat_rate_for(p)

        # Listing does not know the concrete selected offer row (and its allow_additional_promotions flag).
        # To keep behaviour consistent with product detail and cart, we do NOT stack promo on top of an
        # already discounted offer unless explicitly allowed. Here we approximate this by disabling stacking
        # when the representative offer price is lower than the representative list price.
        is_discounted_offer = bool(base_net and list_net and base_net < list_net)
        allow_additional_promotions = not is_discounted_offer

        sale_net, _rule = apply_promo_to_unit_net(
            base_unit_net=base_net,
            site_id=int(site_id),
            channel=channel,
            category_id=p.category_id,
            brand_id=p.brand_id,
            product_id=p.id,
            variant_id=None,
            customer_group_id=None,
            allow_additional_promotions=allow_additional_promotions,
            is_discounted_offer=is_discounted_offer,
        )

        compare_at_price = None
        discount_percent = _discount_percent(list_unit_net=list_net, sale_unit_net=sale_net)
        if discount_percent is not None:
            compare_at_price = _money_out(currency="EUR", unit_net=list_net, vat_rate=rate)

        imgs = list(p.images.all())
        imgs.sort(key=lambda i: (i.sort_order, i.id))
        images_out = []
        for img in imgs:
            if not img.url:
                continue

            # For product grid/listing use square (1:1) renditions if available.
            list_avif = img.listing_avif_url or None
            list_webp = img.listing_webp_url or None
            list_url = list_avif or list_webp or img.url
            images_out.append(
                {
                    "avif_url": list_avif or (img.avif_url or None),
                    "webp_url": list_webp or (img.webp_url or None),
                    "url": list_url,
                    "alt_text": img.alt_text,
                    "sort_order": img.sort_order,
                }
            )
            if len(images_out) >= 2:
                break

        out.append(
            {
                "id": p.id,
                "sku": p.sku,
                "slug": p.slug,
                "name": p.name,
                "is_active": bool(p.is_active),
                "brand": {
                    "id": p.brand.id,
                    "slug": p.brand.slug,
                    "name": p.brand.name,
                }
                if p.brand
                else None,
                "category": {
                    "id": p.category.id,
                    "slug": p.category.slug,
                    "name": p.category.name,
                }
                if p.category
                else None,
                "images": images_out,
                "price": _money_out(currency="EUR", unit_net=Decimal(sale_net), vat_rate=rate),
                "compare_at_price": compare_at_price,
                "discount_percent": discount_percent,
            }
        )

    return out


@router.get("/products/facets", response=CatalogFacetsOut)
def product_facets(
    request,
    country_code: str | None = None,
    channel: str = "normal",
    q: str | None = None,
    category_slug: str | None = None,
    brand_slug: str | None = None,
    group_code: str | None = None,
    feature: str | None = None,
    option: str | None = None,
):
    country_code = normalize_country_code(country_code) or get_request_country_code(request)

    channel = (channel or "normal").strip().lower()
    if channel not in {"normal", "outlet"}:
        raise HttpError(400, "Invalid channel")

    visibility = (
        InventoryItem.OfferVisibility.OUTLET
        if channel == "outlet"
        else InventoryItem.OfferVisibility.NORMAL
    )

    offer_filter = (
        Q(variants__is_active=True)
        & Q(variants__inventory_items__offer_visibility=visibility)
        & Q(variants__inventory_items__qty_on_hand__gt=F("variants__inventory_items__qty_reserved"))
    )
    site_id = _get_request_site_id(request)

    selected_category: Category | None = None

    qs = Product.objects.filter(is_active=True).annotate(
        _has_offer=Count("id", filter=offer_filter)
    )
    if channel == "outlet":
        qs = qs.filter(_has_offer__gt=0)

    meili_dist: dict | None = None
    if q:
        qv = q.strip()
        if qv:
            try:
                index_uid = str(getattr(settings, "MEILI_PRODUCTS_INDEX", "products_lt_v1"))

                category_ancestor_ids = None
                if category_slug:
                    selected_category = Category.objects.filter(slug=category_slug, is_active=True).first()
                    if not selected_category:
                        raise HttpError(404, "Category not found")
                    category_ancestor_ids = list(_ancestor_ids_for_ids(ids={int(selected_category.id)}))

                b_id = None
                if brand_slug:
                    b = Brand.objects.filter(slug=brand_slug, is_active=True).first()
                    if not b:
                        raise HttpError(404, "Brand not found")
                    b_id = int(b.id)

                g_id = None
                if group_code:
                    g = ProductGroup.objects.filter(code=group_code, is_active=True).first()
                    if not g:
                        raise HttpError(404, "Product group not found")
                    g_id = int(g.id)

                fv_ids: list[int] = []
                for f_code, f_val in _parse_pairs(feature):
                    fv = (
                        FeatureValue.objects.filter(
                            feature__code=f_code,
                            value=f_val,
                            is_active=True,
                            feature__is_active=True,
                            feature__is_filterable=True,
                        )
                        .values_list("id", flat=True)
                        .first()
                    )
                    if fv:
                        fv_ids.append(int(fv))

                ov_ids: list[int] = []
                for o_type, o_val in _parse_pairs(option):
                    ov = (
                        OptionValue.objects.filter(
                            option_type__code=o_type,
                            code=o_val,
                            is_active=True,
                            option_type__is_active=True,
                        )
                        .values_list("id", flat=True)
                        .first()
                    )
                    if ov:
                        ov_ids.append(int(ov))

                fres = search_products_facets(
                    q=qv,
                    index_uid=index_uid,
                    site_id=site_id,
                    channel=channel,
                    in_stock_only=False,
                    category_ancestor_ids=category_ancestor_ids,
                    brand_id=b_id,
                    group_id=g_id,
                    feature_value_ids=fv_ids or None,
                    option_value_ids=ov_ids or None,
                )
                meili_dist = fres.distribution
            except MeiliError:
                meili_dist = None

        if meili_dist is None and qv:
            qs = qs.filter(Q(name__icontains=qv) | Q(slug__icontains=qv) | Q(sku__icontains=qv))

    if category_slug:
        selected_category = Category.objects.filter(slug=category_slug, is_active=True).first()
        if not selected_category:
            raise HttpError(404, "Category not found")
        ids = _descendant_category_ids(root_id=int(selected_category.id))
        selected_category_ids = [int(i) for i in ids]
        qs = qs.filter(category_id__in=selected_category_ids)

    if brand_slug:
        b = Brand.objects.filter(slug=brand_slug, is_active=True).first()
        if not b:
            raise HttpError(404, "Brand not found")
        qs = qs.filter(brand_id=b.id)

    if group_code:
        g = ProductGroup.objects.filter(code=group_code, is_active=True).first()
        if not g:
            raise HttpError(404, "Product group not found")
        qs = qs.filter(group_id=g.id)

    for f_code, f_val in _parse_pairs(feature):
        qs = qs.filter(
            feature_values__feature__code=f_code,
            feature_values__feature_value__value=f_val,
        )

    for o_type, o_val in _parse_pairs(option):
        qs = qs.filter(
            variants__option_values__option_type__code=o_type,
            variants__option_values__option_value__code=o_val,
        )

    if feature or option:
        qs = qs.distinct()

    qs = _apply_site_assortment_to_product_qs(
        qs=qs,
        site_id=site_id,
        selected_category_id=(int(selected_category.id) if selected_category is not None else None),
    )

    product_ids: list[int] = []
    if meili_dist is None:
        product_ids = list(qs.values_list("id", flat=True))
        if not product_ids:
            return {
                "categories": [],
                "brands": [],
                "product_groups": [],
                "features": [],
                "option_types": [],
            }

    if selected_category:
        cat_qs = Category.objects.filter(is_active=True, parent_id=selected_category.id).order_by("name")
    else:
        cat_qs = Category.objects.filter(is_active=True, parent_id__isnull=True).order_by("name")

    categories_out = []
    cat_ids = list(cat_qs.values_list("id", flat=True))
    if cat_ids:
        # Category facet should include children even if products are in deeper descendants.
        if meili_dist is not None:
            raw = meili_dist.get("category_id") or {}
            product_category_ids: set[int] = set()
            if isinstance(raw, dict):
                for k in raw.keys():
                    try:
                        product_category_ids.add(int(k))
                    except Exception:
                        continue
        else:
            product_category_ids = set(
                Product.objects.filter(is_active=True, id__in=product_ids)
                .exclude(category_id__isnull=True)
                .values_list("category_id", flat=True)
            )
        allowed: set[int] = set()
        for child_id in cat_ids:
            desc_ids = set(_descendant_category_ids(root_id=int(child_id)))
            if product_category_ids.intersection(desc_ids):
                allowed.add(int(child_id))

        for c in cat_qs:
            if int(c.id) not in allowed:
                continue
            categories_out.append(
                {
                    "id": c.id,
                    "slug": c.slug,
                    "name": c.name,
                    "parent_id": c.parent_id,
                    "description": c.description or "",
                    "hero_image_url": (
                        (c.hero_image.url if getattr(c, "hero_image", None) else "")
                        or (getattr(c, "hero_image_url", "") or "")
                        or None
                    ),
                    "menu_icon_url": (
                        (c.menu_icon.url if getattr(c, "menu_icon", None) else "")
                        or (getattr(c, "menu_icon_url", "") or "")
                        or None
                    ),
                    "seo_title": getattr(c, "seo_title", "") or "",
                    "seo_description": getattr(c, "seo_description", "") or "",
                    "seo_keywords": getattr(c, "seo_keywords", "") or "",
                }
            )

    if meili_dist is not None:
        raw = meili_dist.get("brand_id") or {}
        brand_ids = []
        if isinstance(raw, dict):
            for k in raw.keys():
                try:
                    brand_ids.append(int(k))
                except Exception:
                    continue
        brands_qs = Brand.objects.filter(is_active=True, id__in=brand_ids).order_by("name")

        raw = meili_dist.get("group_id") or {}
        group_ids = []
        if isinstance(raw, dict):
            for k in raw.keys():
                try:
                    group_ids.append(int(k))
                except Exception:
                    continue
        groups_qs = ProductGroup.objects.filter(is_active=True, id__in=group_ids).order_by("name")
    else:
        brands_qs = (
            Brand.objects.filter(is_active=True, products__id__in=product_ids)
            .distinct()
            .order_by("name")
        )
        groups_qs = (
            ProductGroup.objects.filter(is_active=True, products__id__in=product_ids)
            .distinct()
            .order_by("name")
        )

    features_out: list[FeatureOut] = []
    if meili_dist is not None:
        raw = meili_dist.get("feature_value_ids") or {}
        fv_ids: list[int] = []
        if isinstance(raw, dict):
            for k in raw.keys():
                try:
                    fv_ids.append(int(k))
                except Exception:
                    continue
        fvs = FeatureValue.objects.filter(id__in=fv_ids, is_active=True, feature__is_active=True, feature__is_filterable=True).select_related("feature")
        by_feature: dict[int, dict] = {}
        for fv in fvs:
            fid = int(fv.feature_id)
            block = by_feature.get(fid)
            if block is None:
                block = {
                    "id": fid,
                    "code": fv.feature.code,
                    "name": fv.feature.name,
                    "values": [],
                }
                by_feature[fid] = block
            block["values"].append({"id": int(fv.id), "value": str(fv.value)})
        features_out = list(by_feature.values())
    else:
        feature_ids = list(
            ProductFeatureValue.objects.filter(product_id__in=product_ids)
            .values_list("feature_id", flat=True)
            .distinct()
        )
        features_qs = (
            Feature.objects.filter(is_active=True, is_filterable=True, id__in=feature_ids)
            .prefetch_related("values")
            .order_by("sort_order", "code")
        )
        for f in features_qs:
            used_vals = set(
                ProductFeatureValue.objects.filter(product_id__in=product_ids, feature_id=f.id)
                .values_list("feature_value__value", flat=True)
            )
            vals = [v for v in f.values.all() if v.is_active and v.value in used_vals]
            vals.sort(key=lambda v: (v.sort_order, v.value, v.id))
            features_out.append(
                {
                    "id": f.id,
                    "code": f.code,
                    "name": f.name,
                    "values": [{"id": v.id, "value": v.value} for v in vals],
                }
            )

    option_types_out: list[OptionTypeOut] = []
    if meili_dist is not None:
        opt_field = "option_value_ids_in_stock_outlet" if channel == "outlet" else "option_value_ids_in_stock_normal"
        raw = meili_dist.get(opt_field) or {}
        ov_ids: list[int] = []
        if isinstance(raw, dict):
            for k in raw.keys():
                try:
                    ov_ids.append(int(k))
                except Exception:
                    continue
        ovs = OptionValue.objects.filter(id__in=ov_ids, is_active=True, option_type__is_active=True).select_related("option_type")
        by_type: dict[int, dict] = {}
        for ov in ovs:
            tid = int(ov.option_type_id)
            block = by_type.get(tid)
            if block is None:
                t = ov.option_type
                block = {
                    "id": tid,
                    "code": t.code,
                    "name": t.name,
                    "display_type": t.display_type,
                    "swatch_type": t.swatch_type,
                    "values": [],
                }
                by_type[tid] = block
            block["values"].append({"id": int(ov.id), "code": ov.code, "label": ov.label})
        option_types_out = list(by_type.values())
    else:
        option_type_ids = list(
            VariantOptionValue.objects.filter(variant__product_id__in=product_ids)
            .values_list("option_type_id", flat=True)
            .distinct()
        )
        option_types_qs = (
            OptionType.objects.filter(is_active=True, id__in=option_type_ids)
            .prefetch_related("values")
            .order_by("sort_order", "code")
        )
        for t in option_types_qs:
            used_codes = set(
                VariantOptionValue.objects.filter(variant__product_id__in=product_ids, option_type_id=t.id)
                .values_list("option_value__code", flat=True)
                .distinct()
            )
            vals = [v for v in t.values.all() if v.is_active and v.code in used_codes]
            vals.sort(key=lambda v: (v.sort_order, v.label, v.id))
            option_types_out.append(
                {
                    "id": t.id,
                    "code": t.code,
                    "name": t.name,
                    "display_type": t.display_type,
                    "swatch_type": t.swatch_type,
                    "values": [{"id": v.id, "code": v.code, "label": v.label} for v in vals],
                }
            )

    return {
        "categories": categories_out,
        "brands": [{"id": b.id, "slug": b.slug, "name": b.name} for b in brands_qs],
        "product_groups": [
            {"id": g.id, "code": g.code, "name": g.name, "description": g.description or ""}
            for g in groups_qs
        ],
        "features": features_out,
        "option_types": option_types_out,
    }


@router.get("/categories/{slug}/products", response=list[ProductListOut])
@paginate(ProductPagination)
def category_products(
    request,
    slug: str,
    country_code: str | None = None,
    channel: str = "normal",
    q: str | None = None,
    brand_slug: str | None = None,
    group_code: str | None = None,
    feature: str | None = None,
    option: str | None = None,
    sort: str | None = None,
    in_stock_only: bool = False,
):
    return products(
        request,
        country_code=country_code,
        channel=channel,
        q=q,
        category_slug=slug,
        brand_slug=brand_slug,
        group_code=group_code,
        feature=feature,
        option=option,
        sort=sort,
        in_stock_only=in_stock_only,
    )


@router.get("/brands/{slug}/products", response=list[ProductListOut])
@paginate(ProductPagination)
def brand_products(
    request,
    slug: str,
    country_code: str | None = None,
    channel: str = "normal",
    q: str | None = None,
    category_slug: str | None = None,
    group_code: str | None = None,
    feature: str | None = None,
    option: str | None = None,
    sort: str | None = None,
    in_stock_only: bool = False,
):
    return products(
        request,
        country_code=country_code,
        channel=channel,
        q=q,
        category_slug=category_slug,
        brand_slug=slug,
        group_code=group_code,
        feature=feature,
        option=option,
        sort=sort,
        in_stock_only=in_stock_only,
    )


@router.get("/product-groups/{code}/products", response=list[ProductListOut])
@paginate(ProductPagination)
def product_group_products(
    request,
    code: str,
    country_code: str | None = None,
    channel: str = "normal",
    q: str | None = None,
    category_slug: str | None = None,
    brand_slug: str | None = None,
    feature: str | None = None,
    option: str | None = None,
    sort: str | None = None,
    in_stock_only: bool = False,
):
    return products(
        request,
        country_code=country_code,
        channel=channel,
        q=q,
        category_slug=category_slug,
        brand_slug=brand_slug,
        group_code=code,
        feature=feature,
        option=option,
        sort=sort,
        in_stock_only=in_stock_only,
    )


@router.post("/back-in-stock/subscribe", response=BackInStockSubscribeOut)
def back_in_stock_subscribe(request, payload: BackInStockSubscribeIn):
    email = (payload.email or "").strip().lower()
    try:
        validate_email(email)
    except ValidationError:
        raise HttpError(400, "Invalid email")

    channel = (getattr(payload, "channel", None) or "normal").strip().lower()
    if channel not in {"normal", "outlet"}:
        raise HttpError(400, "Invalid channel")

    product = None
    variant = None

    if payload.variant_id:
        variant = (
            Variant.objects.filter(id=int(payload.variant_id))
            .select_related("product")
            .first()
        )
        if not variant:
            raise HttpError(404, "Variant not found")
        product = variant.product

    if payload.product_id:
        product = Product.objects.filter(id=int(payload.product_id)).first()
        if not product:
            raise HttpError(404, "Product not found")

    if not product and not variant:
        raise HttpError(400, "product_id or variant_id is required")

    language_code = get_request_language_code(request)

    site = getattr(request, "site", None)
    site_id = int(getattr(site, "id", 0) or 0) or None
    if site_id is None:
        raise HttpError(400, "Site is not resolved")

    obj, created = BackInStockSubscription.objects.get_or_create(
        site_id=int(site_id),
        email=email,
        product=product,
        variant=variant,
        channel=channel,
        defaults={"is_active": True, "language_code": language_code},
    )
    if not created and not obj.is_active:
        obj.is_active = True
        obj.save(update_fields=["is_active"])

    if not created and (obj.language_code or "").strip().lower() != (language_code or "").strip().lower():
        obj.language_code = language_code
        obj.save(update_fields=["language_code"])

    return {"status": "ok"}


@router.get("/products/{slug}", response=ProductDetailOut)
def product_detail(
    request,
    slug: str,
    country_code: str | None = None,
    channel: str = "normal",
    language_code: str | None = None,
):
    country_code = normalize_country_code(country_code) or get_request_country_code(request)

    channel = (channel or "normal").strip().lower()
    if channel not in {"normal", "outlet"}:
        raise HttpError(400, "Invalid channel")

    site_id = _get_request_site_id(request)

    from .models import (
        FeatureTranslation,
        FeatureValueTranslation,
        OptionTypeTranslation,
        OptionValueTranslation,
        ProductTranslation,
    )

    language_code = language_code or _get_language_code(request)
    fallback_langs = translation_fallback_chain(language_code)

    product_qs = Product.objects.filter(slug=slug, is_active=True)
    product_qs = _apply_site_assortment_to_product_qs(
        qs=product_qs,
        site_id=site_id,
        selected_category_id=None,
    )

    product = (
        product_qs
        .select_related("brand", "category", "tax_class")
        .prefetch_related(
            "images",
            "translations",
            "feature_values__feature",
            "feature_values__feature_value",
            "feature_values__feature__translations",
            "feature_values__feature_value__translations",
            "variants",
            "variants__option_values__option_type",
            "variants__option_values__option_value",
            "variants__option_values__option_type__translations",
            "variants__option_values__option_value__translations",
            "variants__inventory_items",
        )
        .first()
    )
    if not product:
        raise HttpError(404, "Product not found")

    try:
        # Some frontends prefetch product detail endpoints (e.g. link/router prefetch).
        # Do not treat prefetch requests as real product views.
        purpose = str(request.headers.get("Purpose") or request.headers.get("Sec-Purpose") or "").strip().lower()
        if purpose != "prefetch":
            track_event(
                request=request,
                name="product_view",
                object_type="product",
                object_id=int(product.id),
                payload={"slug": product.slug, "sku": product.sku},
                country_code=country_code,
                channel=channel,
                language_code=(language_code or ""),
            )
    except Exception:
        pass

    if not product.tax_class_id:
        raise HttpError(400, "Product has no tax_class assigned")

    try:
        vat_rate = get_vat_rate(country_code=country_code,
                                tax_class=product.tax_class)
    except LookupError:
        raise HttpError(400, "VAT rate not configured for country/tax_class")

    images = list(product.images.all())
    images.sort(key=lambda i: (i.sort_order, i.id))

    variants_qs = [v for v in product.variants.all() if v.is_active]
    variants_qs.sort(key=lambda v: (v.sku, v.id))

    variants: list[VariantOut] = []
    delivery_window_out = None
    best_delivery_min = None
    for v in variants_qs:
        inv_all = list(v.inventory_items.all())

        visibility = (
            InventoryItem.OfferVisibility.OUTLET
            if channel == "outlet"
            else InventoryItem.OfferVisibility.NORMAL
        )
        inv = [ii for ii in inv_all if ii.offer_visibility == visibility]

        stock = sum([ii.qty_available for ii in inv]) if inv else 0

        best_offer: InventoryItem | None = None
        inv_available = [ii for ii in inv if ii.qty_available > 0]
        if inv_available:
            inv_available.sort(
                key=lambda ii: (
                    -int(ii.offer_priority or 0),
                    _effective_offer_unit_net(list_unit_net=Decimal(v.price_eur), offer=ii),
                    int(ii.id),
                )
            )
            best_offer = inv_available[0]

        if best_offer and product is not None:
            dw = estimate_delivery_window(
                now=timezone.now(),
                site_id=_get_request_site_id(request),
                country_code=country_code,
                channel=channel,
                warehouse_id=int(best_offer.warehouse_id) if best_offer.warehouse_id else None,
                product_id=int(product.id),
                brand_id=int(product.brand_id) if product.brand_id else None,
                category_id=int(product.category_id) if product.category_id else None,
                product_group_id=int(product.group_id) if getattr(product, "group_id", None) else None,
            )
            if dw is not None:
                if best_delivery_min is None or dw.min_date < best_delivery_min:
                    best_delivery_min = dw.min_date
                    delivery_window_out = {
                        "min_date": dw.min_date.isoformat(),
                        "max_date": dw.max_date.isoformat(),
                        "kind": dw.kind,
                        "rule_code": dw.rule_code,
                        "source": dw.source,
                    }

        list_unit_net = Decimal(v.price_eur)
        base_unit_net = (
            _effective_offer_unit_net(list_unit_net=list_unit_net, offer=best_offer)
            if best_offer
            else list_unit_net
        )

        is_discounted_offer = bool(
            best_offer
            and (not bool(getattr(best_offer, "never_discount", False)))
            and (
                best_offer.offer_price_override_eur is not None
                or best_offer.offer_discount_percent is not None
            )
        )

        site_id = _get_request_site_id(request)
        if site_id is None:
            site_id = 0

        sale_unit_net, _rule = apply_promo_to_unit_net(
            base_unit_net=base_unit_net,
            site_id=int(site_id),
            channel=channel,
            category_id=product.category_id,
            brand_id=product.brand_id,
            product_id=product.id,
            variant_id=v.id,
            customer_group_id=None,
            allow_additional_promotions=bool(getattr(best_offer, "allow_additional_promotions", False)) if best_offer else False,
            is_discounted_offer=is_discounted_offer,
        )

        compare_base = list_unit_net
        disc_pct = _discount_percent(list_unit_net=list_unit_net, sale_unit_net=sale_unit_net)

        options = list(v.option_values.select_related(
            "option_type", "option_value").all())
        options.sort(key=lambda r: (
            r.option_type.sort_order, r.option_type.code))

        option_type_name_by_id = {}
        option_value_label_by_id = {}
        for r in options:
            if r.option_type_id and r.option_type_id not in option_type_name_by_id:
                ot_translations = _translations_of(r.option_type, OptionTypeTranslation)
                ott = _pick_best_translation(translations=ot_translations, fallback_langs=fallback_langs)
                option_type_name_by_id[r.option_type_id] = (getattr(ott, "name", "") or r.option_type.name)

            if r.option_value_id and r.option_value_id not in option_value_label_by_id:
                ov_translations = _translations_of(r.option_value, OptionValueTranslation)
                ovt = _pick_best_translation(translations=ov_translations, fallback_langs=fallback_langs)
                option_value_label_by_id[r.option_value_id] = (getattr(ovt, "label", "") or r.option_value.label)

        variants.append(
            {
                "id": v.id,
                "sku": v.sku,
                "barcode": v.barcode,
                "name": v.name,
                "is_active": bool(v.is_active),
                "stock_available": int(stock),
                "price": _money_out(currency="EUR", unit_net=sale_unit_net, vat_rate=Decimal(vat_rate)),
                "compare_at_price": (
                    _money_out(currency="EUR", unit_net=compare_base, vat_rate=Decimal(vat_rate))
                    if disc_pct is not None
                    else None
                ),
                "offer_id": (int(best_offer.id) if best_offer else None),
                "offer_label": (best_offer.offer_label if best_offer else ""),
                "condition_grade": (best_offer.condition_grade if best_offer else ""),
                "offer_visibility": (best_offer.offer_visibility if best_offer else ""),
                "discount_percent": disc_pct,
                "options": [
                    {
                        "option_type_code": r.option_type.code,
                        "option_type_name": option_type_name_by_id.get(r.option_type_id) or r.option_type.name,
                        "option_value_code": r.option_value.code,
                        "option_value_label": option_value_label_by_id.get(r.option_value_id) or r.option_value.label,
                    }
                    for r in options
                ],
            }
        )

    feature_rows = list(
        ProductFeatureValue.objects.filter(product_id=product.id)
        .select_related("feature", "feature_value")
        .order_by("feature__sort_order", "feature__code", "feature_value__sort_order", "feature_value__value")
    )

    feature_name_by_id = {}
    feature_value_by_id = {}
    for r in feature_rows:
        if r.feature_id not in feature_name_by_id:
            f_translations = _translations_of(r.feature, FeatureTranslation)
            ft = _pick_best_translation(translations=f_translations, fallback_langs=fallback_langs)
            feature_name_by_id[r.feature_id] = (getattr(ft, "name", "") or r.feature.name)
        if r.feature_value_id not in feature_value_by_id:
            v_translations = _translations_of(r.feature_value, FeatureValueTranslation)
            vt = _pick_best_translation(translations=v_translations, fallback_langs=fallback_langs)
            feature_value_by_id[r.feature_value_id] = (getattr(vt, "value", "") or r.feature_value.value)

    features_out = [
        {
            "feature_id": r.feature_id,
            "feature_code": r.feature.code,
            "feature_name": feature_name_by_id.get(r.feature_id) or r.feature.name,
            "value_id": r.feature_value_id,
            "value": feature_value_by_id.get(r.feature_value_id) or r.feature_value.value,
        }
        for r in feature_rows
    ]

    site = getattr(request, "site", None)
    site_id = int(getattr(site, "id", 0) or 0) if site is not None else None

    content_blocks = get_content_blocks_for_product(
        site_id=site_id,
        product_id=int(product.id),
        placement="product_detail",
        channel=channel,
        brand_id=getattr(product, "brand_id", None),
        category_id=getattr(product, "category_id", None),
        product_group_id=getattr(getattr(product, "group", None), "id", None),
        language_code=language_code,
    )

    p_translations = _translations_of(product, ProductTranslation)
    pt = _pick_best_translation(translations=p_translations, fallback_langs=fallback_langs)

    return {
        "id": product.id,
        "sku": product.sku,
        "slug": (getattr(pt, "slug", "") or product.slug),
        "name": (getattr(pt, "name", "") or product.name),
        "description": (getattr(pt, "description", "") or product.description),
        "is_active": bool(product.is_active),
        "seo_title": (getattr(pt, "seo_title", "") or getattr(product, "seo_title", "") or ""),
        "seo_description": (getattr(pt, "seo_description", "") or getattr(product, "seo_description", "") or ""),
        "seo_keywords": (getattr(pt, "seo_keywords", "") or getattr(product, "seo_keywords", "") or ""),
        "brand": {
            "id": product.brand.id,
            "slug": product.brand.slug,
            "name": product.brand.name,
        }
        if product.brand
        else None,
        "category": {
            "id": product.category.id,
            "slug": product.category.slug,
            "name": product.category.name,
        }
        if product.category
        else None,
        "images": [
            {
                "avif_url": img.avif_url or None,
                "webp_url": img.webp_url or None,
                "url": img.url,
                "alt_text": img.alt_text,
                "sort_order": img.sort_order,
            }
            for img in images
            if img.url
        ],
        "features": features_out,
        "delivery_window": delivery_window_out,
        "content_blocks": [
            {
                "key": b.key,
                "title": b.title,
                "placement": b.placement,
                "type": b.type,
                "payload": b.payload,
            }
            for b in content_blocks
        ],
        "variants": variants,
    }
