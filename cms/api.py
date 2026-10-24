from __future__ import annotations

from django.db.models import Q
from django.utils import timezone
from ninja import Router
from ninja.errors import HttpError

from api.i18n import get_request_language_code
from api.shop_context import get_request_shop, shop_context_enabled, shop_context_strict

from .models import (
    CmsCategory, CmsCategoryTranslation, CmsPage, CmsPageTranslation,
    MegaMenuFeature, Menu, MenuItem, StoreLocation,
)
from .schemas import (
    CmsCategoryOut, CmsPageListOut, CmsPageOut, MegaMenuFeatureOut,
    NavItemOut, NavigationOut, StoreLocationOut,
)
from .services import translation_fallback_chain


router = Router(tags=["cms"])


def _pick_best_translation(
    *,
    page_id: int,
    language_code: str | None,
    shop=None,
    only_fields: tuple[str, ...],
):
    langs = translation_fallback_chain(language_code, shop=shop)
    translations = list(
        CmsPageTranslation.objects.filter(
            cms_page_id=page_id,
            language_code__in=langs,
        ).only(*only_fields)
    )
    order_index = {lang: i for i, lang in enumerate(langs)}
    best = None
    best_idx = 10_000
    for t in translations:
        idx = order_index.get((t.language_code or "").lower(), 10_000)
        if idx < best_idx:
            best = t
            best_idx = idx
    return best


@router.get("/pages/{slug}", response=CmsPageOut)
def cms_page_detail(request, slug: str, language_code: str | None = None):
    if language_code is None:
        language_code = get_request_language_code(request)

    shop = get_request_shop(request)
    if shop_context_enabled() and shop_context_strict() and shop is None:
        raise HttpError(404, "Shop not resolved for host")

    page_qs = CmsPage.objects.filter(slug=slug, is_active=True)
    if shop is not None:
        page_qs = page_qs.filter(shop_id=shop.id)
    else:
        page_qs = page_qs.filter(shop__isnull=True)

    page = page_qs.only("id", "slug", "updated_at").first()
    if page is None:
        raise HttpError(404, "Page not found")

    best = _pick_best_translation(
        page_id=int(page.id),
        language_code=language_code,
        shop=shop,
        only_fields=(
            "language_code",
            "title",
            "seo_title",
            "seo_description",
            "hero_image",
            "hero_image_alt",
            "body_markdown",
        ),
    )

    title = getattr(best, "title", "") or ""
    seo_title = getattr(best, "seo_title", "") or ""
    seo_description = getattr(best, "seo_description", "") or ""
    body_markdown = getattr(best, "body_markdown", "") or ""
    hero_image = getattr(best, "hero_image", None)
    hero_image_alt = getattr(best, "hero_image_alt", "") or ""

    # Unify FE rendering: page content is delivered as content blocks.
    content_blocks = []

    if hero_image:
        src = getattr(hero_image, "url", "")
        if src:
            content_blocks.append(
                {
                    "type": "image",
                    "payload": {
                        "src": src,
                        "alt": hero_image_alt,
                    },
                }
            )

    if body_markdown.strip():
        content_blocks.append({"type": "rich_text", "payload": {"markdown": body_markdown}})

    return {
        "slug": page.slug,
        "title": title,
        "seo_title": seo_title,
        "seo_description": seo_description,
        "updated_at": page.updated_at or timezone.now(),
        "content_blocks": content_blocks,
    }


def _shop_scoped(qs, request):
    shop = get_request_shop(request)
    if shop_context_enabled() and shop_context_strict() and shop is None:
        raise HttpError(404, "Shop not resolved for host")
    if shop is not None:
        return qs.filter(shop_id=shop.id)
    return qs.filter(shop__isnull=True)


def _best_category_translation(*, category_id: int, language_code: str | None, shop):
    langs = translation_fallback_chain(language_code, shop=shop)
    translations = list(
        CmsCategoryTranslation.objects.filter(
            category_id=category_id, language_code__in=langs)
        .only("language_code", "name", "description"))
    order_index = {lang: i for i, lang in enumerate(langs)}
    best, best_idx = None, 10_000
    for t in translations:
        idx = order_index.get((t.language_code or "").lower(), 10_000)
        if idx < best_idx:
            best, best_idx = t, idx
    return best


@router.get("/categories", response=list[CmsCategoryOut])
def cms_categories(request, language_code: str | None = None):
    if language_code is None:
        language_code = get_request_language_code(request)
    shop = get_request_shop(request)
    cats = _shop_scoped(
        CmsCategory.objects.filter(is_active=True), request)
    out = []
    for c in cats.prefetch_related("translations"):
        page_qs = c.pages.filter(is_active=True)
        if shop is not None:
            page_qs = page_qs.filter(shop_id=shop.id)
        else:
            page_qs = page_qs.filter(shop__isnull=True)
        best = _best_category_translation(
            category_id=int(c.id), language_code=language_code, shop=shop)
        out.append({
            "slug": c.slug,
            "name": getattr(best, "name", "") or "",
            "description": getattr(best, "description", "") or "",
            "icon": c.icon or "",
            "page_count": page_qs.count(),
        })
    return out


@router.get("/categories/{slug}/pages", response=list[CmsPageListOut])
def cms_category_pages(request, slug: str, language_code: str | None = None):
    if language_code is None:
        language_code = get_request_language_code(request)
    shop = get_request_shop(request)
    cat = _shop_scoped(
        CmsCategory.objects.filter(slug=slug, is_active=True), request
    ).first()
    if cat is None:
        raise HttpError(404, "Category not found")
    out = []
    for p in cat.pages.filter(is_active=True).order_by("sort_order", "slug"):
        if shop is not None:
            if p.shop_id != shop.id:
                continue
        elif p.shop_id is not None:
            continue
        best = _pick_best_translation(
            page_id=int(p.id), language_code=language_code, shop=shop,
            only_fields=("language_code", "title"))
        out.append({
            "slug": p.slug,
            "title": getattr(best, "title", "") or "",
            "updated_at": p.updated_at or timezone.now(),
        })
    return out


PRESET_URLS = {
    "discounted": "/katalogas?sort=discounted",
    "new": "/katalogas?sort=-created",
    "bestsellers": "/katalogas?sort=best_selling",
    "all_categories": "/kategorijos",
    "all_products": "/katalogas",
    "gift_cards": "/katalogas",
    "help": "/pagalba",
    "stores": "/parduotuves",
    "favorites": "/megstamos",
    "account": "/paskyra",
    "cart": "/krepselis",
}


def _nav_version() -> int:
    from django.core.cache import cache
    if cache.add("cms:nav:ver", 1):
        return 1
    return int(cache.get("cms:nav:ver") or 1)


def bump_nav_version() -> None:
    """post_save/post_delete on Menu/MenuItem/MegaMenuFeature family."""
    from django.core.cache import cache
    try:
        cache.incr("cms:nav:ver")
    except ValueError:
        cache.set("cms:nav:ver", 2)


def _best_item_translation(item, langs: list[str]):
    order_index = {lang: i for i, lang in enumerate(langs)}
    best, best_idx = None, 10_000
    for t in item.translations.all():
        idx = order_index.get((t.language_code or "").lower(), 10_000)
        if idx < best_idx:
            best, best_idx = t, idx
    return best


def _serialize_nav_item(item, *, language_code: str, langs: list[str], lang_rank: dict):
    from catalog.localization import localize_brand, localize_category

    tr = _best_item_translation(item, langs)
    kind = item.kind
    url = ""
    label = ""
    if kind == MenuItem.Kind.LINK:
        url = item.link_url or ""
    elif kind == MenuItem.Kind.PRESET:
        url = PRESET_URLS.get(item.preset or "", "")
    elif kind == MenuItem.Kind.CATEGORY and item.category_id:
        loc = localize_category(item.category, language_code=language_code)
        url = f"/k/{loc['slug']}"
        label = loc["name"]
    elif kind == MenuItem.Kind.BRAND and item.brand_id:
        loc = localize_brand(item.brand, language_code=language_code)
        url = f"/g/{loc['slug']}"
        label = loc["name"]
    elif kind == MenuItem.Kind.CMS_PAGE and item.cms_page_id:
        best_page_tr = None
        best_idx = 10_000
        for t in item.cms_page.translations.all():
            idx = lang_rank.get((t.language_code or "").lower(), 10_000)
            if idx < best_idx:
                best_page_tr, best_idx = t, idx
        url = f"/info/{item.cms_page.slug}"
        label = (getattr(best_page_tr, "title", "") or item.cms_page.slug)

    if tr is not None and tr.title:
        label = tr.title

    return NavItemOut(
        id=int(item.id),
        kind=kind,
        label=label,
        url=url,
        icon=item.icon or "",
        badge=(tr.badge if tr and tr.badge else (item.badge or "")),
        highlight=bool(item.highlight),
        new_tab=bool(item.open_new_tab),
        show_in=item.show_in or "all",
        children=[],
    )


@router.get("/navigation", response=NavigationOut)
def cms_navigation(request, language_code: str | None = None):
    if language_code is None:
        language_code = get_request_language_code(request)
    shop = get_request_shop(request)
    shop_key = str(shop.id) if shop else "null"
    cache_key = f"cms:nav:v{_nav_version()}:{shop_key}:{language_code}"
    from django.core.cache import cache
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    langs = translation_fallback_chain(language_code, shop=shop)
    lang_rank = {lang: i for i, lang in enumerate(langs)}

    if shop_context_enabled() and shop_context_strict() and shop is None:
        raise HttpError(404, "Shop not resolved for host")

    def _menu(code: str):
        """Shop-specific menu wins; global (shop=None) is the fallback
        so a seeded default serves every storefront."""
        base = Menu.objects.filter(code=code, is_active=True)
        if shop is not None:
            mine = base.filter(shop_id=shop.id).first()
            if mine is not None:
                return mine
        return base.filter(shop__isnull=True).first()

    # Header tree — one query for the whole forest, recurse in memory.
    items_out: list[NavItemOut] = []
    header = _menu("header")
    if header is not None:
        all_items = list(
            header.items.filter(is_active=True)
            .select_related("category", "brand", "cms_page")
            .prefetch_related(
                "translations", "category__translations",
                "brand__translations", "cms_page__translations",
            )
            .order_by("tree_id", "lft")
        )
        by_parent: dict[int | None, list] = {}
        for it in all_items:
            by_parent.setdefault(it.parent_id, []).append(it)

        def _build(item):
            out = _serialize_nav_item(
                item, language_code=language_code,
                langs=langs, lang_rank=lang_rank)
            out.children = [
                _build(ch) for ch in by_parent.get(item.id, [])]
            return out

        items_out = [_build(r) for r in by_parent.get(None, [])]

    # Promo strip — first active root item of the "promo" menu.
    promo_out = None
    promo = _menu("promo")
    if promo is not None:
        it = (
            promo.items.filter(is_active=True, parent__isnull=True)
            .prefetch_related("translations")
            .order_by("tree_id", "lft").first()
        )
        if it is not None:
            promo_out = _serialize_nav_item(
                it, language_code=language_code,
                langs=langs, lang_rank=lang_rank)

    # Mega-menu promo cards keyed by root category ("default" = fallback).
    # Shop-specific cards are merged over the global defaults.
    mega_features: dict[str, MegaMenuFeatureOut] = {}
    feats_qs = MegaMenuFeature.objects.filter(is_active=True)
    if shop is not None:
        feats_qs = feats_qs.filter(Q(shop_id=shop.id) | Q(shop__isnull=True))
        feats = sorted(
            feats_qs.prefetch_related("translations"),
            key=lambda f: (0 if f.shop_id else 1, f.sort_order, f.id))
    else:
        feats = feats_qs.filter(shop__isnull=True).prefetch_related(
            "translations").order_by("sort_order", "id")
    for f in feats:
        key = str(f.category_id) if f.category_id else "default"
        if key in mega_features:
            continue
        tr = _best_item_translation(f, langs)
        mega_features[key] = MegaMenuFeatureOut(
            title=(getattr(tr, "title", "") or ""),
            text=(getattr(tr, "text", "") or ""),
            image_url=f.image_resolved or "",
            url=f.link_url or "",
            cta_label=(getattr(tr, "cta_label", "") or ""),
        )

    out = NavigationOut(
        promo=promo_out, items=items_out, mega_features=mega_features)
    cache.set(cache_key, out, 3600)
    return out


@router.get("/stores", response=list[StoreLocationOut])
def cms_stores(request):
    qs = _shop_scoped(StoreLocation.objects.filter(is_active=True), request)
    return [
        {
            "id": s.id,
            "name": s.name,
            "line1": s.line1,
            "city": s.city,
            "postal_code": s.postal_code or "",
            "country_code": s.country_code or "",
            "phone": s.phone or "",
            "email": s.email or "",
            "latitude": str(s.latitude) if s.latitude is not None else None,
            "longitude": str(s.longitude) if s.longitude is not None else None,
            "opening_hours": s.opening_hours or {},
            "services": s.services or [],
            "description": s.description or "",
            "is_pickup_point": bool(s.is_pickup_point),
        }
        for s in qs
    ]
