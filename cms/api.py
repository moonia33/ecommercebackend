from __future__ import annotations

from django.utils import timezone
from ninja import Router
from ninja.errors import HttpError

from api.i18n import get_request_language_code
from api.models import SiteConfig

from .models import CmsPage, CmsPageTranslation, NavigationItem, SiteNavigation
from .schemas import CmsPageOut, NavigationOut
from .services import translation_fallback_chain


router = Router(tags=["cms"])


def _pick_best_translation(
    *,
    page_id: int,
    language_code: str | None,
    only_fields: tuple[str, ...],
):
    langs = translation_fallback_chain(language_code)
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


def _safe_format_path(template: str, *, slug: str) -> str:
    tpl = (template or "").strip() or "/{slug}"
    s = (slug or "").strip()
    try:
        return tpl.format(slug=s)
    except Exception:
        return f"/{s}"


def _get_site_config(site_id: int | None) -> SiteConfig | None:
    if site_id is None:
        return None
    try:
        return SiteConfig.objects.filter(site_id=int(site_id)).only(
            "category_path_template",
            "brand_path_template",
            "cms_page_path_template",
        ).first()
    except Exception:
        return None


def _resolve_item_href(*, item: NavigationItem, sc: SiteConfig | None) -> str:
    lt = (getattr(item, "link_type", "") or "").strip().lower()
    if lt == "url":
        return (getattr(item, "url", "") or "").strip()

    if lt == "category":
        cat = getattr(item, "category", None)
        slug = getattr(cat, "slug", "") if cat is not None else ""
        tpl = getattr(sc, "category_path_template", "/c/{slug}") if sc is not None else "/c/{slug}"
        return _safe_format_path(tpl, slug=str(slug or ""))

    if lt == "brand":
        brand = getattr(item, "brand", None)
        slug = getattr(brand, "slug", "") if brand is not None else ""
        tpl = getattr(sc, "brand_path_template", "/b/{slug}") if sc is not None else "/b/{slug}"
        return _safe_format_path(tpl, slug=str(slug or ""))

    if lt == "cms_page":
        page = getattr(item, "cms_page", None)
        slug = getattr(page, "slug", "") if page is not None else ""
        tpl = getattr(sc, "cms_page_path_template", "/page/{slug}") if sc is not None else "/page/{slug}"
        return _safe_format_path(tpl, slug=str(slug or ""))

    return ""


def _resolve_item_image_src(*, item: NavigationItem) -> str:
    img = getattr(item, "image", None)
    if img:
        try:
            u = getattr(img, "url", "")
            if u:
                return u
        except Exception:
            pass
    return (getattr(item, "image_url", "") or "").strip()


@router.get("/navigation/{code}", response=NavigationOut)
def navigation(request, code: str, language_code: str | None = None):
    if language_code is None:
        language_code = get_request_language_code(request)

    site = getattr(request, "site", None)
    site_id = int(getattr(site, "id", 0) or 0) or None

    nav = SiteNavigation.objects.filter(code=(code or "").strip().lower(), is_active=True)
    if site_id is not None:
        nav = nav.filter(site_id=site_id)
    nav = nav.only("id", "code").first()
    if nav is None:
        raise HttpError(404, "Navigation not found")

    sc = _get_site_config(site_id)

    langs = translation_fallback_chain(language_code)
    items = list(
        NavigationItem.objects.filter(navigation_id=int(nav.id), is_active=True)
        .select_related("category", "brand", "cms_page")
        .prefetch_related("translations")
        .only(
            "id",
            "navigation_id",
            "parent_id",
            "sort_order",
            "link_type",
            "url",
            "icon",
            "image",
            "image_url",
            "category_id",
            "brand_id",
            "cms_page_id",
            "open_in_new_tab",
        )
        .order_by("sort_order", "id")
    )

    order_index = {lang: i for i, lang in enumerate(langs)}

    def pick_translation(it: NavigationItem):
        best = None
        best_idx = 10_000
        for t in list(getattr(it, "translations", []).all()):
            idx = order_index.get((getattr(t, "language_code", "") or "").lower(), 10_000)
            if idx < best_idx:
                best_idx = idx
                best = t
        return best

    by_parent: dict[int | None, list[NavigationItem]] = {}
    for it in items:
        by_parent.setdefault(it.parent_id, []).append(it)

    def build(parent_id: int | None):
        out = []
        for it in by_parent.get(parent_id, []):
            t = pick_translation(it)
            out.append(
                {
                    "id": int(it.id),
                    "label": (getattr(t, "label", "") or "") if t is not None else "",
                    "href": _resolve_item_href(item=it, sc=sc),
                    "link_type": (it.link_type or "").strip().lower(),
                    "icon": (getattr(it, "icon", "") or "").strip(),
                    "image_src": _resolve_item_image_src(item=it),
                    "badge": (getattr(t, "badge", "") or "") if t is not None else "",
                    "badge_kind": (getattr(t, "badge_kind", "") or "") if t is not None else "",
                    "open_in_new_tab": bool(getattr(it, "open_in_new_tab", False)),
                    "children": build(int(it.id)),
                }
            )
        return out

    return {"code": nav.code, "items": build(None)}


@router.get("/pages/{slug}", response=CmsPageOut)
def cms_page_detail(request, slug: str, language_code: str | None = None):
    if language_code is None:
        language_code = get_request_language_code(request)

    site = getattr(request, "site", None)

    qs = CmsPage.objects.filter(slug=slug, is_active=True)
    if site is not None and getattr(site, "id", None) is not None:
        qs = qs.filter(site_id=int(site.id))

    page = qs.only("id", "slug", "updated_at").first()
    if page is None:
        raise HttpError(404, "Page not found")

    best = _pick_best_translation(
        page_id=int(page.id),
        language_code=language_code,
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
