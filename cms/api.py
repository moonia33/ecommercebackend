from __future__ import annotations

from django.utils import timezone
from ninja import Router
from ninja.errors import HttpError

from api.i18n import get_request_language_code
from api.shop_context import get_request_shop, shop_context_enabled, shop_context_strict

from .models import CmsCategory, CmsCategoryTranslation, CmsPage, CmsPageTranslation, StoreLocation
from .schemas import CmsCategoryOut, CmsPageListOut, CmsPageOut, StoreLocationOut
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
