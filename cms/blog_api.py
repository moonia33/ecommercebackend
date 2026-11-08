"""Public blog API: /cms/blog/*."""
from __future__ import annotations

from django.db.models import Count, Q
from django.db.models.functions import Greatest
from django.utils import timezone
from ninja.errors import HttpError

from api.i18n import get_request_language_code
from api.shop_context import get_request_shop, shop_context_enabled, shop_context_strict

from .api import router
from .content import products_payload, rich_content_blocks
from .models import BlogCategory, BlogPost, BlogPostTranslation, BlogSlugRedirect
from .schemas import (
    BlogCategoryOut, BlogPostListOut, BlogPostOut, BlogPostPageOut, BlogSitemapEntryOut,
)
from .services import translation_fallback_chain


def _shop(request):
    shop = get_request_shop(request)
    if shop_context_enabled() and shop_context_strict() and shop is None:
        raise HttpError(404, "Shop not resolved for host")
    return shop


def _shop_q(shop, prefix: str = "") -> Q:
    if shop is None:
        return Q(**{f"{prefix}shop__isnull": True})
    return Q(**{f"{prefix}shop__isnull": True}) | Q(**{f"{prefix}shop_id": shop.id})


def published_posts(shop):
    return BlogPost.objects.filter(
        _shop_q(shop), status=BlogPost.Status.PUBLISHED, published_at__lte=timezone.now(),
    )


def _best(translations, langs):
    order = {lang: i for i, lang in enumerate(langs)}
    ranked = sorted(
        (t for t in translations if (t.language_code or "").lower() in order),
        key=lambda t: order[(t.language_code or "").lower()],
    )
    return ranked[0] if ranked else None


def _category_out(cat, langs, post_count: int = 0) -> BlogCategoryOut:
    tr = _best(list(cat.translations.all()), langs)
    return BlogCategoryOut(
        slug=cat.slug, name=(tr.name if tr else cat.slug),
        description=(tr.description if tr else ""), post_count=post_count,
    )


def _list_item(post, tr, langs) -> dict:
    return {
        "id": int(post.id),
        "slug": tr.slug,
        "language_code": tr.language_code,
        "title": tr.title,
        "excerpt": tr.excerpt,
        "cover_image": post.cover_image.url if post.cover_image else "",
        "cover_image_alt": tr.cover_image_alt or tr.title,
        "published_at": post.published_at,
        "reading_minutes": int(tr.reading_minutes or 0),
        "author_name": post.author_name or "",
        "categories": [_category_out(c, langs) for c in post.categories.all() if c.is_active],
        "is_featured": bool(post.is_featured),
    }


@router.get("/blog/posts", response=BlogPostPageOut)
def blog_posts(
    request,
    language_code: str | None = None,
    category: str | None = None,
    product: int | None = None,
    catalog_category: int | None = None,
    featured: bool | None = None,
    page: int = 1,
    page_size: int = 12,
):
    language_code = language_code or get_request_language_code(request)
    shop = _shop(request)
    langs = translation_fallback_chain(language_code, shop=shop)
    qs = published_posts(shop).filter(translations__language_code__in=langs).distinct()
    if category:
        qs = qs.filter(categories__slug=category, categories__is_active=True)
    if product:
        qs = qs.filter(related_products__id=product)
    if catalog_category:
        qs = qs.filter(related_categories__id=catalog_category)
    if featured is not None:
        qs = qs.filter(is_featured=featured)
    page = max(1, int(page))
    page_size = max(1, min(int(page_size), 48))
    total = qs.count()
    rows = list(
        qs.order_by("-published_at", "-id")
        .prefetch_related("translations", "categories__translations")[(page - 1) * page_size: page * page_size]
    )
    results = []
    for post in rows:
        tr = _best(list(post.translations.all()), langs)
        if tr is not None:
            results.append(_list_item(post, tr, langs))
    return {"page": page, "page_size": page_size, "total_count": total, "results": results}


def _find_post(shop, slug: str, language_code: str):
    base = published_posts(shop)
    tr = (
        BlogPostTranslation.objects.filter(post__in=base, slug=slug)
        .select_related("post")
        .order_by("-post__shop_id")
    )
    exact = tr.filter(language_code=language_code).first()
    if exact is not None:
        return exact.post, False
    other = tr.first()
    if other is not None:
        return other.post, True
    redirect = (
        BlogSlugRedirect.objects.filter(post__in=base, old_slug=slug)
        .order_by("-created_at").select_related("post").first()
    )
    if redirect is not None:
        return redirect.post, True
    return None, False


@router.get("/blog/posts/{slug}", response=BlogPostOut)
def blog_post_detail(request, slug: str, language_code: str | None = None):
    language_code = language_code or get_request_language_code(request)
    shop = _shop(request)
    langs = translation_fallback_chain(language_code, shop=shop)
    post, _moved = _find_post(shop, slug, language_code)
    if post is None:
        raise HttpError(404, "Post not found")
    translations = list(post.translations.all())
    tr = _best(translations, langs)
    if tr is None:
        raise HttpError(404, "Post not found")
    data = _list_item(post, tr, langs)
    related_ids = list(post.related_products.values_list("id", flat=True)[:12])
    data.update(
        seo_title=tr.seo_title or tr.title,
        seo_description=tr.seo_description or tr.excerpt,
        updated_at=max(post.updated_at, tr.updated_at),
        content_blocks=rich_content_blocks(tr.body_json, shop=shop, language_code=language_code),
        alternates=[{"language_code": t.language_code, "slug": t.slug} for t in translations],
        related_products=products_payload(related_ids, shop=shop, language_code=language_code),
        redirect_to_slug=(tr.slug if tr.slug != slug else ""),
    )
    return data


@router.get("/blog/categories", response=list[BlogCategoryOut])
def blog_categories(request, language_code: str | None = None):
    language_code = language_code or get_request_language_code(request)
    shop = _shop(request)
    langs = translation_fallback_chain(language_code, shop=shop)
    now = timezone.now()
    cats = (
        BlogCategory.objects.filter(_shop_q(shop), is_active=True)
        .annotate(n=Count(
            "posts", distinct=True,
            filter=Q(posts__status=BlogPost.Status.PUBLISHED, posts__published_at__lte=now)
            & (_shop_q(shop, "posts__")),
        ))
        .prefetch_related("translations")
    )
    return [_category_out(c, langs, int(c.n)) for c in cats]


@router.get("/blog/sitemap", response=list[BlogSitemapEntryOut])
def blog_sitemap(request):
    shop = _shop(request)
    rows = (
        BlogPostTranslation.objects.filter(post__in=published_posts(shop))
        .annotate(last_modified=Greatest("updated_at", "post__updated_at"))
        .values("language_code", "slug", "last_modified")
    )
    return [{"language_code": r["language_code"], "slug": r["slug"], "updated_at": r["last_modified"]} for r in rows]
