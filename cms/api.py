from __future__ import annotations

from django.utils import timezone
from ninja import Router
from ninja.errors import HttpError

from api.i18n import get_request_language_code

from .models import CmsPage, CmsPageTranslation
from .schemas import CmsPageOut
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


@router.get("/pages/{slug}", response=CmsPageOut)
def cms_page_detail(request, slug: str, language_code: str | None = None):
    if language_code is None:
        language_code = get_request_language_code(request)

    page = CmsPage.objects.filter(slug=slug, is_active=True).only("id", "slug", "updated_at").first()
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
