"""Ops (ecommerceadmin) API for CMS pages, blog posts and editor media: /ops/cms/*."""
from __future__ import annotations

import json
from datetime import datetime

from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from django.utils.text import slugify
from ninja import Schema
from ninja.errors import HttpError
from ninja.files import UploadedFile

from api.i18n import get_default_language_code, get_supported_languages, validate_supported_language_code
from checkout.ops.shared import _auth, _ensure_shop_context_if_strict, _require_ops_user, router

from .models import (
    BlogCategory, BlogCategoryTranslation, BlogPost, BlogPostTranslation, BlogSlugRedirect,
    CmsCategory, CmsMedia, CmsPage, CmsPageTranslation,
)

IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp", "image/avif", "image/gif"}
MAX_IMAGE_BYTES = 10 * 1024 * 1024
MAX_DOC_BYTES = 1024 * 1024


# --- schemas -----------------------------------------------------------------

class OpsCmsOption(Schema):
    id: int | None = None
    code: str = ""
    name: str = ""


class OpsCmsLookupsOut(Schema):
    default_language: str
    languages: list[OpsCmsOption]
    shops: list[OpsCmsOption]
    page_categories: list[OpsCmsOption]
    blog_categories: list[OpsCmsOption]


class OpsCmsMediaOut(Schema):
    id: int
    url: str
    alt_text: str = ""
    width: int | None = None
    height: int | None = None


class OpsCmsPageTranslationIn(Schema):
    language_code: str
    title: str = ""
    seo_title: str = ""
    seo_description: str = ""
    hero_image_alt: str = ""
    body_json: dict | None = None
    remove_hero_image: bool = False


class OpsCmsPageTranslationOut(Schema):
    language_code: str
    title: str = ""
    seo_title: str = ""
    seo_description: str = ""
    hero_image: str = ""
    hero_image_alt: str = ""
    body_json: dict | None = None
    body_markdown: str = ""


class OpsCmsPageIn(Schema):
    slug: str
    shop_id: int | None = None
    category_id: int | None = None
    sort_order: int = 0
    is_active: bool = True
    translations: list[OpsCmsPageTranslationIn] = []


class OpsCmsPageListItem(Schema):
    id: int
    slug: str
    shop_id: int | None = None
    shop_code: str = ""
    category: str = ""
    title: str = ""
    is_active: bool
    languages: list[str] = []
    uses_editor: bool = False
    updated_at: datetime


class OpsCmsPageOut(OpsCmsPageListItem):
    category_id: int | None = None
    sort_order: int = 0
    translations: list[OpsCmsPageTranslationOut] = []


class OpsCmsPagePaged(Schema):
    page: int
    page_size: int
    total_count: int
    results: list[OpsCmsPageListItem]


class OpsBlogTranslationIn(Schema):
    language_code: str
    slug: str = ""
    title: str = ""
    excerpt: str = ""
    body_json: dict | None = None
    seo_title: str = ""
    seo_description: str = ""
    cover_image_alt: str = ""


class OpsBlogTranslationOut(Schema):
    language_code: str
    slug: str
    title: str
    excerpt: str = ""
    body_json: dict | None = None
    seo_title: str = ""
    seo_description: str = ""
    cover_image_alt: str = ""
    reading_minutes: int = 0


class OpsBlogPostIn(Schema):
    shop_id: int | None = None
    status: str = "draft"
    published_at: datetime | None = None
    author_name: str = ""
    is_featured: bool = False
    category_ids: list[int] = []
    related_product_ids: list[int] = []
    related_category_ids: list[int] = []
    remove_cover_image: bool = False
    translations: list[OpsBlogTranslationIn] = []


class OpsBlogProductRef(Schema):
    id: int
    sku: str = ""
    name: str = ""
    image: str = ""


class OpsBlogPostListItem(Schema):
    id: int
    shop_id: int | None = None
    shop_code: str = ""
    status: str
    state: str
    published_at: datetime | None = None
    title: str = ""
    slug: str = ""
    languages: list[str] = []
    cover_image: str = ""
    author_name: str = ""
    is_featured: bool = False
    updated_at: datetime


class OpsBlogPostOut(OpsBlogPostListItem):
    category_ids: list[int] = []
    related_products: list[OpsBlogProductRef] = []
    related_category_ids: list[int] = []
    translations: list[OpsBlogTranslationOut] = []


class OpsBlogPostPaged(Schema):
    page: int
    page_size: int
    total_count: int
    results: list[OpsBlogPostListItem]


class OpsBlogCategoryTranslationIn(Schema):
    language_code: str
    name: str = ""
    description: str = ""


class OpsBlogCategoryIn(Schema):
    slug: str
    shop_id: int | None = None
    sort_order: int = 0
    is_active: bool = True
    translations: list[OpsBlogCategoryTranslationIn] = []


class OpsBlogCategoryOut(Schema):
    id: int
    slug: str
    shop_id: int | None = None
    sort_order: int = 0
    is_active: bool = True
    translations: list[OpsBlogCategoryTranslationIn] = []


class OpsCmsOk(Schema):
    ok: bool = True


# --- helpers -----------------------------------------------------------------

def _guard(request):
    _ensure_shop_context_if_strict(request)
    return _require_ops_user(request)


def _lang(code: str) -> str:
    try:
        return validate_supported_language_code(code)
    except ValueError:
        raise HttpError(400, f"Nepalaikoma kalba: {code}")


def _slug(raw: str, fallback: str = "") -> str:
    value = slugify((raw or "").strip() or fallback, allow_unicode=False)[:200]
    if not value:
        raise HttpError(400, "Slug privalomas")
    return value


def _doc(value):
    if value is None:
        return None
    if not isinstance(value, dict) or value.get("type") != "doc":
        raise HttpError(400, "Turinys turi būti Tiptap dokumentas")
    if len(json.dumps(value)) > MAX_DOC_BYTES:
        raise HttpError(400, "Turinys per didelis")
    return value


def _validate_image(file: UploadedFile):
    if (file.content_type or "") not in IMAGE_TYPES:
        raise HttpError(400, "Netinkamas failas — reikalingas JPEG/PNG/WebP/AVIF/GIF")
    if (file.size or 0) > MAX_IMAGE_BYTES:
        raise HttpError(400, "Failas per didelis (daugiausia 10 MB)")
    from PIL import Image

    try:
        with Image.open(file) as img:
            img.verify()
            size = img.size
    except Exception:
        raise HttpError(400, "Failas nėra tinkamas paveikslėlis")
    file.seek(0)
    return size


def _shop_exists(shop_id):
    if shop_id is None:
        return None
    from api.models import Shop

    if not Shop.objects.filter(id=shop_id).exists():
        raise HttpError(400, "Parduotuvė nerasta")
    return shop_id


def _pick(translations, attr: str = "title"):
    default = get_default_language_code()
    rows = sorted(translations, key=lambda t: (t.language_code != default, t.language_code))
    for t in rows:
        if getattr(t, attr, ""):
            return t
    return rows[0] if rows else None


@router.get("/cms/lookups", response=OpsCmsLookupsOut, auth=_auth)
def ops_cms_lookups(request):
    _guard(request)
    from api.models import Shop

    default = get_default_language_code()
    cat_rows = []
    for c in CmsCategory.objects.prefetch_related("translations").order_by("sort_order", "slug"):
        tr = _pick(list(c.translations.all()), "name")
        cat_rows.append(OpsCmsOption(id=c.id, code=c.slug, name=(tr.name if tr else c.slug)))
    blog_rows = []
    for c in BlogCategory.objects.prefetch_related("translations"):
        tr = _pick(list(c.translations.all()), "name")
        blog_rows.append(OpsCmsOption(id=c.id, code=c.slug, name=(tr.name if tr else c.slug)))
    return OpsCmsLookupsOut(
        default_language=default,
        languages=[OpsCmsOption(code=c, name=n) for c, n in get_supported_languages()],
        shops=[OpsCmsOption(id=s.id, code=s.code, name=s.name) for s in Shop.objects.order_by("id")],
        page_categories=cat_rows,
        blog_categories=blog_rows,
    )


@router.post("/cms/media", response=OpsCmsMediaOut, auth=_auth)
def ops_cms_upload_media(request, file: UploadedFile, alt_text: str = ""):
    user = _guard(request)
    width, height = _validate_image(file)
    media = CmsMedia.objects.create(
        image=file, alt_text=alt_text[:255], width=width, height=height, uploaded_by=user)
    return OpsCmsMediaOut(
        id=media.id, url=media.image.url, alt_text=media.alt_text, width=width, height=height)


# --- CMS pages ---------------------------------------------------------------

def _page_item(page) -> dict:
    translations = list(page.translations.all())
    tr = _pick(translations)
    cat = page.category
    return {
        "id": page.id, "slug": page.slug, "shop_id": page.shop_id,
        "shop_code": page.shop.code if page.shop_id else "",
        "category": cat.slug if cat else "",
        "title": tr.title if tr else "",
        "is_active": page.is_active,
        "languages": sorted(t.language_code for t in translations),
        "uses_editor": any(t.body_json for t in translations),
        "updated_at": page.updated_at,
    }


def _page_out(page) -> dict:
    data = _page_item(page)
    data.update(
        category_id=page.category_id, sort_order=page.sort_order,
        translations=[
            {
                "language_code": t.language_code, "title": t.title,
                "seo_title": t.seo_title, "seo_description": t.seo_description,
                "hero_image": t.hero_image.url if t.hero_image else "",
                "hero_image_alt": t.hero_image_alt, "body_json": t.body_json,
                "body_markdown": t.body_markdown,
            }
            for t in sorted(page.translations.all(), key=lambda t: t.language_code)
        ],
    )
    return data


def _page_qs():
    return CmsPage.objects.select_related("shop", "category").prefetch_related("translations")


def _get_page(page_id: int):
    page = _page_qs().filter(id=page_id).first()
    if page is None:
        raise HttpError(404, "Puslapis nerastas")
    return page


@router.get("/cms/pages", response=OpsCmsPagePaged, auth=_auth)
def ops_cms_pages(request, q: str = "", shop_id: int | None = None, page: int = 1, page_size: int = 50):
    _guard(request)
    qs = _page_qs().order_by("slug", "shop_id")
    if q.strip():
        qs = qs.filter(Q(slug__icontains=q.strip()) | Q(translations__title__icontains=q.strip())).distinct()
    if shop_id is not None:
        qs = qs.filter(shop_id=shop_id)
    page = max(1, page)
    page_size = max(1, min(page_size, 200))
    total = qs.count()
    rows = [_page_item(p) for p in qs[(page - 1) * page_size: page * page_size]]
    return {"page": page, "page_size": page_size, "total_count": total, "results": rows}


@router.get("/cms/pages/{page_id}", response=OpsCmsPageOut, auth=_auth)
def ops_cms_page(request, page_id: int):
    _guard(request)
    return _page_out(_get_page(page_id))


def _save_page(page: CmsPage | None, payload: OpsCmsPageIn) -> CmsPage:
    slug = _slug(payload.slug)
    shop_id = _shop_exists(payload.shop_id)
    clash = CmsPage.objects.filter(slug=slug, shop_id=shop_id)
    if page is not None:
        clash = clash.exclude(id=page.id)
    if clash.exists():
        raise HttpError(400, f"Puslapis „{slug}“ šiai parduotuvei jau yra")
    if payload.category_id is not None and not CmsCategory.objects.filter(id=payload.category_id).exists():
        raise HttpError(400, "Kategorija nerasta")
    with transaction.atomic():
        if page is None:
            page = CmsPage()
        page.slug = slug
        page.shop_id = shop_id
        page.category_id = payload.category_id
        page.sort_order = payload.sort_order
        page.is_active = payload.is_active
        page.save()
        for item in payload.translations:
            code = _lang(item.language_code)
            tr, _ = CmsPageTranslation.objects.get_or_create(cms_page=page, language_code=code)
            tr.title = item.title.strip()[:255]
            tr.seo_title = item.seo_title.strip()[:255]
            tr.seo_description = item.seo_description.strip()
            tr.hero_image_alt = item.hero_image_alt.strip()[:255]
            tr.body_json = _doc(item.body_json)
            if item.remove_hero_image and tr.hero_image:
                tr.hero_image.delete(save=False)
                tr.hero_image = None
            tr.save()
    return page


@router.post("/cms/pages", response=OpsCmsPageOut, auth=_auth)
def ops_cms_create_page(request, payload: OpsCmsPageIn):
    _guard(request)
    return _page_out(_get_page(_save_page(None, payload).id))


@router.patch("/cms/pages/{page_id}", response=OpsCmsPageOut, auth=_auth)
def ops_cms_update_page(request, page_id: int, payload: OpsCmsPageIn):
    _guard(request)
    return _page_out(_get_page(_save_page(_get_page(page_id), payload).id))


@router.post("/cms/pages/{page_id}/hero", response=OpsCmsPageOut, auth=_auth)
def ops_cms_page_hero(request, page_id: int, language_code: str, file: UploadedFile):
    _guard(request)
    page = _get_page(page_id)
    _validate_image(file)
    tr, _ = CmsPageTranslation.objects.get_or_create(cms_page=page, language_code=_lang(language_code))
    tr.hero_image = file
    tr.save()
    return _page_out(_get_page(page_id))


# --- blog posts --------------------------------------------------------------

def _post_state(post) -> str:
    if post.status != BlogPost.Status.PUBLISHED:
        return "draft"
    if post.published_at and post.published_at > timezone.now():
        return "scheduled"
    return "published"


def _post_item(post) -> dict:
    translations = list(post.translations.all())
    tr = _pick(translations)
    return {
        "id": post.id, "shop_id": post.shop_id,
        "shop_code": post.shop.code if post.shop_id else "",
        "status": post.status, "state": _post_state(post), "published_at": post.published_at,
        "title": tr.title if tr else "", "slug": tr.slug if tr else "",
        "languages": sorted(t.language_code for t in translations),
        "cover_image": post.cover_image.url if post.cover_image else "",
        "author_name": post.author_name, "is_featured": post.is_featured,
        "updated_at": post.updated_at,
    }


def _post_out(post) -> dict:
    data = _post_item(post)
    products = []
    for p in post.related_products.all().prefetch_related("images"):
        img = next(iter(p.images.all()), None)
        products.append({
            "id": p.id, "sku": p.sku or "", "name": p.name or "",
            "image": img.image.url if img is not None and img.image else "",
        })
    data.update(
        category_ids=[c.id for c in post.categories.all()],
        related_products=products,
        related_category_ids=[c.id for c in post.related_categories.all()],
        translations=[
            {
                "language_code": t.language_code, "slug": t.slug, "title": t.title,
                "excerpt": t.excerpt, "body_json": t.body_json, "seo_title": t.seo_title,
                "seo_description": t.seo_description, "cover_image_alt": t.cover_image_alt,
                "reading_minutes": t.reading_minutes,
            }
            for t in sorted(post.translations.all(), key=lambda t: t.language_code)
        ],
    )
    return data


def _post_qs():
    return BlogPost.objects.select_related("shop").prefetch_related("translations")


def _get_post(post_id: int):
    post = _post_qs().filter(id=post_id).first()
    if post is None:
        raise HttpError(404, "Įrašas nerastas")
    return post


@router.get("/cms/blog/posts", response=OpsBlogPostPaged, auth=_auth)
def ops_blog_posts(
    request, q: str = "", state: str = "", shop_id: int | None = None, page: int = 1, page_size: int = 50,
):
    _guard(request)
    now = timezone.now()
    qs = _post_qs().order_by("-updated_at")
    if q.strip():
        qs = qs.filter(Q(translations__title__icontains=q.strip()) | Q(translations__slug__icontains=q.strip())).distinct()
    if shop_id is not None:
        qs = qs.filter(shop_id=shop_id)
    if state == "draft":
        qs = qs.filter(status=BlogPost.Status.DRAFT)
    elif state == "scheduled":
        qs = qs.filter(status=BlogPost.Status.PUBLISHED, published_at__gt=now)
    elif state == "published":
        qs = qs.filter(status=BlogPost.Status.PUBLISHED, published_at__lte=now)
    page = max(1, page)
    page_size = max(1, min(page_size, 200))
    total = qs.count()
    rows = [_post_item(p) for p in qs[(page - 1) * page_size: page * page_size]]
    return {"page": page, "page_size": page_size, "total_count": total, "results": rows}


@router.get("/cms/blog/posts/{post_id}", response=OpsBlogPostOut, auth=_auth)
def ops_blog_post(request, post_id: int):
    _guard(request)
    return _post_out(_get_post(post_id))


def _save_post(post: BlogPost | None, payload: OpsBlogPostIn, user) -> BlogPost:
    from catalog.models import Category, Product

    if payload.status not in BlogPost.Status.values:
        raise HttpError(400, "Neteisinga būsena")
    shop_id = _shop_exists(payload.shop_id)
    items = []
    seen = set()
    for item in payload.translations:
        code = _lang(item.language_code)
        if code in seen:
            raise HttpError(400, f"Kalba {code} nurodyta kelis kartus")
        seen.add(code)
        title = item.title.strip()[:255]
        if not title:
            continue
        slug = _slug(item.slug, title)
        clash = BlogPostTranslation.objects.filter(language_code=code, slug=slug)
        if shop_id is not None:
            clash = clash.filter(Q(post__shop_id=shop_id) | Q(post__shop__isnull=True))
        if post is not None:
            clash = clash.exclude(post_id=post.id)
        if clash.exists():
            raise HttpError(400, f"Slug „{slug}“ ({code}) jau naudojamas kitame įraše")
        items.append((code, slug, title, item))
    if payload.status == BlogPost.Status.PUBLISHED and not items:
        raise HttpError(400, "Publikavimui reikia bent vieno vertimo su pavadinimu")

    with transaction.atomic():
        if post is None:
            post = BlogPost(author=user)
            if not payload.author_name.strip():
                post.author_name = (user.get_full_name() or "").strip() if hasattr(user, "get_full_name") else ""
        post.shop_id = shop_id
        post.status = payload.status
        post.published_at = payload.published_at
        if post.published_at is not None and timezone.is_naive(post.published_at):
            post.published_at = timezone.make_aware(post.published_at)
        if post.status == BlogPost.Status.PUBLISHED and post.published_at is None:
            post.published_at = timezone.now()
        if payload.author_name.strip():
            post.author_name = payload.author_name.strip()[:120]
        post.is_featured = payload.is_featured
        if payload.remove_cover_image and post.cover_image:
            post.cover_image.delete(save=False)
            post.cover_image = None
        post.save()
        post.categories.set(BlogCategory.objects.filter(id__in=payload.category_ids))
        post.related_products.set(Product.objects.filter(id__in=payload.related_product_ids[:50]))
        post.related_categories.set(Category.objects.filter(id__in=payload.related_category_ids[:50]))

        post.translations.exclude(language_code__in=[i[0] for i in items]).delete()
        existing = {t.language_code: t for t in post.translations.all()}
        for code, slug, title, item in items:
            tr = existing.get(code) or BlogPostTranslation(post=post, language_code=code)
            if tr.pk and tr.slug and tr.slug != slug:
                BlogSlugRedirect.objects.get_or_create(post=post, language_code=code, old_slug=tr.slug)
            stale = BlogSlugRedirect.objects.filter(language_code=code, old_slug=slug)
            if shop_id is not None:
                stale = stale.filter(Q(post__shop_id=shop_id) | Q(post__shop__isnull=True))
            if not (post.status == BlogPost.Status.PUBLISHED and post.published_at <= timezone.now()):
                # A draft must not steal a live post's old permalink.
                stale = stale.filter(post=post)
            stale.delete()
            tr.slug = slug
            tr.title = title
            tr.excerpt = item.excerpt.strip()
            tr.body_json = _doc(item.body_json)
            tr.seo_title = item.seo_title.strip()[:255]
            tr.seo_description = item.seo_description.strip()
            tr.cover_image_alt = item.cover_image_alt.strip()[:255]
            tr.save()
    return post


@router.post("/cms/blog/posts", response=OpsBlogPostOut, auth=_auth)
def ops_blog_create_post(request, payload: OpsBlogPostIn):
    user = _guard(request)
    return _post_out(_get_post(_save_post(None, payload, user).id))


@router.patch("/cms/blog/posts/{post_id}", response=OpsBlogPostOut, auth=_auth)
def ops_blog_update_post(request, post_id: int, payload: OpsBlogPostIn):
    user = _guard(request)
    return _post_out(_get_post(_save_post(_get_post(post_id), payload, user).id))


@router.delete("/cms/blog/posts/{post_id}", response=OpsCmsOk, auth=_auth)
def ops_blog_delete_post(request, post_id: int):
    _guard(request)
    post = _get_post(post_id)
    if post.status == BlogPost.Status.PUBLISHED:
        raise HttpError(400, "Publikuoto įrašo trinti negalima — pirma grąžinkite į juodraštį")
    post.delete()
    return OpsCmsOk()


@router.post("/cms/blog/posts/{post_id}/cover", response=OpsBlogPostOut, auth=_auth)
def ops_blog_post_cover(request, post_id: int, file: UploadedFile):
    _guard(request)
    post = _get_post(post_id)
    _validate_image(file)
    post.cover_image = file
    post.save(update_fields=["cover_image", "updated_at"])
    return _post_out(_get_post(post_id))


# --- blog categories ---------------------------------------------------------

def _category_out(cat) -> dict:
    return {
        "id": cat.id, "slug": cat.slug, "shop_id": cat.shop_id, "sort_order": cat.sort_order,
        "is_active": cat.is_active,
        "translations": [
            {"language_code": t.language_code, "name": t.name, "description": t.description}
            for t in cat.translations.all()
        ],
    }


@router.get("/cms/blog/categories", response=list[OpsBlogCategoryOut], auth=_auth)
def ops_blog_categories(request):
    _guard(request)
    return [_category_out(c) for c in BlogCategory.objects.prefetch_related("translations")]


def _save_category(cat: BlogCategory | None, payload: OpsBlogCategoryIn) -> BlogCategory:
    slug = _slug(payload.slug)
    shop_id = _shop_exists(payload.shop_id)
    clash = BlogCategory.objects.filter(slug=slug, shop_id=shop_id)
    if cat is not None:
        clash = clash.exclude(id=cat.id)
    if clash.exists():
        raise HttpError(400, f"Kategorija „{slug}“ jau yra")
    with transaction.atomic():
        cat = cat or BlogCategory()
        cat.slug = slug
        cat.shop_id = shop_id
        cat.sort_order = payload.sort_order
        cat.is_active = payload.is_active
        cat.save()
        for item in payload.translations:
            code = _lang(item.language_code)
            if not item.name.strip():
                BlogCategoryTranslation.objects.filter(category=cat, language_code=code).delete()
                continue
            BlogCategoryTranslation.objects.update_or_create(
                category=cat, language_code=code,
                defaults={"name": item.name.strip()[:200], "description": item.description.strip()},
            )
    return cat


@router.post("/cms/blog/categories", response=OpsBlogCategoryOut, auth=_auth)
def ops_blog_create_category(request, payload: OpsBlogCategoryIn):
    _guard(request)
    return _category_out(_save_category(None, payload))


@router.patch("/cms/blog/categories/{category_id}", response=OpsBlogCategoryOut, auth=_auth)
def ops_blog_update_category(request, category_id: int, payload: OpsBlogCategoryIn):
    _guard(request)
    cat = BlogCategory.objects.filter(id=category_id).first()
    if cat is None:
        raise HttpError(404, "Kategorija nerasta")
    return _category_out(_save_category(cat, payload))
