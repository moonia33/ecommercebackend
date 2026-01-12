from __future__ import annotations

from django.core.cache import cache
from django.utils import timezone
from ninja import Router
from ninja.errors import HttpError

from api.i18n import get_request_language_code

from catalog.home_services import get_products_by_slugs_for_grid, get_products_for_grid
from catalog.models import Category

from cms.services import translation_fallback_chain

from .models import (
    CategoryGridSection,
    HeroSection,
    HomePage,
    HomePageTranslation,
    HomeSection,
    HomeSectionTranslation,
    NewsletterSection,
    ProductGridSection,
    RichTextSection,
)
from .schemas import HomeOut


router = Router(tags=["home"])


def _descendant_category_ids(*, root_id: int) -> list[int]:
    rows = Category.objects.filter(is_active=True).values("id", "parent_id")
    children: dict[int, list[int]] = {}
    for r in rows:
        pid = int(r.get("parent_id") or 0)
        cid = int(r["id"])
        children.setdefault(pid, []).append(cid)

    out: list[int] = []
    stack = [int(root_id)]
    seen: set[int] = set()
    while stack:
        cur = stack.pop()
        if cur in seen:
            continue
        seen.add(cur)
        out.append(cur)
        for child in children.get(cur, []):
            if child not in seen:
                stack.append(child)
    return out


def _pick_best_translation(*, qs, language_code: str | None):
    langs = translation_fallback_chain(language_code)
    rows = list(qs.filter(language_code__in=langs))
    order_index = {lang: i for i, lang in enumerate(langs)}
    best = None
    best_idx = 10_000
    for r in rows:
        idx = order_index.get((r.language_code or "").lower(), 10_000)
        if idx < best_idx:
            best = r
            best_idx = idx
    return best


@router.get("/home", response=HomeOut)
def home(
    request,
    country_code: str = "LT",
    channel: str = "normal",
    language_code: str | None = None,
):
    if language_code is None:
        language_code = get_request_language_code(request)

    cache_key = f"homebuilder:home:v1:cc:{(country_code or '').upper()}:ch:{(channel or '').lower()}:lang:{(language_code or '').lower()}"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    page = HomePage.objects.filter(code="home", is_active=True).only("id", "code", "updated_at").first()
    if page is None:
        raise HttpError(404, "Home page not configured")

    page_t = _pick_best_translation(qs=HomePageTranslation.objects.filter(home_page_id=page.id), language_code=language_code)

    title = getattr(page_t, "title", "") or ""
    seo_title = getattr(page_t, "seo_title", "") or ""
    seo_description = getattr(page_t, "seo_description", "") or ""

    sections = list(
        HomeSection.objects.filter(home_page_id=page.id, is_active=True).order_by("sort_order", "id")
    )

    out_sections: list[dict] = []

    for s in sections:
        tbest = _pick_best_translation(qs=HomeSectionTranslation.objects.filter(home_section_id=s.id), language_code=language_code)
        title_sec = getattr(tbest, "title", "") or ""

        if s.type == HomeSection.Type.HERO:
            hero = HeroSection.objects.filter(home_section_id=s.id).first()
            if hero is None:
                continue
            slides = list(hero.slides.all())
            slides_out = []
            for sl in slides:
                sl_t = _pick_best_translation(qs=sl.translations.all(), language_code=language_code)
                img_src = sl.image.url if sl.image else (sl.image_url or "")
                if not img_src:
                    continue
                slides_out.append(
                    {
                        "image": {"src": img_src, "alt": sl.image_alt},
                        "title": getattr(sl_t, "title", "") or "",
                        "subtitle": getattr(sl_t, "subtitle", "") or "",
                        "cta": {"label": getattr(sl_t, "cta_label", "") or "", "url": sl.cta_url or ""},
                    }
                )
            out_sections.append({"type": "hero", "payload": {"title": title_sec, "slides": slides_out}})
            continue

        if s.type == HomeSection.Type.PRODUCT_GRID:
            grid = ProductGridSection.objects.filter(home_section_id=s.id).select_related("category", "brand", "product_group").first()
            if grid is None:
                continue

            limit = max(0, min(48, int(grid.limit or 0)))

            pinned_rows = list(grid.pinned.select_related("product").all())
            pinned_rows.sort(key=lambda r: (r.sort_order, r.id))
            pinned_slugs = [r.product.slug for r in pinned_rows if r.product_id]

            pinned_items = get_products_by_slugs_for_grid(
                country_code=country_code,
                channel=channel,
                product_slugs=pinned_slugs,
                in_stock_only=True,
            )
            pinned_ids = {int(p["id"]) for p in pinned_items if isinstance(p, dict) and p.get("id") is not None}

            in_stock_only = bool(grid.in_stock_only)
            if grid.stock_policy == ProductGridSection.StockPolicy.HIDE_OOS:
                in_stock_only = True

            remaining = max(0, limit - len(pinned_items))

            grid_items: list[dict] = []
            if remaining > 0:
                grid_items = get_products_for_grid(
                    country_code=country_code,
                    channel=channel,
                    q=grid.q or None,
                    category_slug=grid.category.slug if grid.category_id else None,
                    brand_slug=grid.brand.slug if grid.brand_id else None,
                    group_code=grid.product_group.code if grid.product_group_id else None,
                    feature=grid.feature or None,
                    option=grid.option or None,
                    sort=grid.sort or None,
                    in_stock_only=in_stock_only,
                    limit=remaining,
                    exclude_product_ids=pinned_ids,
                )

            items = pinned_items + grid_items

            payload = {
                "title": title_sec,
                "limit": limit,
                "stock_policy": grid.stock_policy,
                "pinned": {"position": "start"},
                "source": {
                    "kind": "listing",
                    "category_slug": grid.category.slug if grid.category_id else None,
                    "brand_slug": grid.brand.slug if grid.brand_id else None,
                    "group_code": grid.product_group.code if grid.product_group_id else None,
                    "q": grid.q or None,
                    "feature": grid.feature or None,
                    "option": grid.option or None,
                    "sort": grid.sort or None,
                    "in_stock_only": in_stock_only,
                },
            }

            out_sections.append({"type": "product_grid", "payload": payload, "items": items})
            continue

        if s.type == HomeSection.Type.CATEGORY_GRID:
            grid = CategoryGridSection.objects.filter(home_section_id=s.id).select_related("root_category").first()
            if grid is None:
                continue

            limit = max(0, min(30, int(grid.limit or 0)))

            if grid.root_category_id:
                # rules: descendants of root
                ids = _descendant_category_ids(root_id=int(grid.root_category_id))
                cats = list(Category.objects.filter(is_active=True, id__in=ids).order_by("name"))
            else:
                pinned = list(grid.pinned.select_related("category").all())
                pinned.sort(key=lambda r: (r.sort_order, r.id))
                cats = [r.category for r in pinned if r.category_id]

            if limit:
                cats = cats[:limit]

            items = [
                {
                    "id": int(c.id),
                    "slug": c.slug,
                    "name": c.name,
                    "hero_image_url": getattr(c, "hero_url", "") or None,
                    "menu_icon_url": getattr(c, "menu_icon_url_resolved", "") or None,
                }
                for c in cats
            ]

            payload = {
                "title": title_sec,
                "limit": limit,
                "source": {
                    "kind": "rules" if grid.root_category_id else "manual",
                    "root_slug": grid.root_category.slug if grid.root_category_id else None,
                },
            }

            out_sections.append({"type": "category_grid", "payload": payload, "items": items})
            continue

        if s.type == HomeSection.Type.RICH_TEXT:
            rt = RichTextSection.objects.filter(home_section_id=s.id).first()
            if rt is None:
                continue
            best = _pick_best_translation(qs=rt.translations.all(), language_code=language_code)
            md = getattr(best, "markdown", "") or ""
            out_sections.append({"type": "rich_text", "payload": {"title": title_sec, "markdown": md}})
            continue

        if s.type == HomeSection.Type.NEWSLETTER:
            nl = NewsletterSection.objects.filter(home_section_id=s.id).first()
            if nl is None:
                continue
            best = _pick_best_translation(qs=nl.translations.all(), language_code=language_code)
            out_sections.append(
                {
                    "type": "newsletter",
                    "payload": {
                        "title": getattr(best, "title", "") or title_sec,
                        "subtitle": getattr(best, "subtitle", "") or "",
                        "cta_label": getattr(best, "cta_label", "") or "",
                    },
                }
            )
            continue

    out = {
        "code": page.code,
        "title": title,
        "seo_title": seo_title,
        "seo_description": seo_description,
        "updated_at": page.updated_at or timezone.now(),
        "sections": out_sections,
    }
    cache.set(cache_key, out, 60)
    return out
