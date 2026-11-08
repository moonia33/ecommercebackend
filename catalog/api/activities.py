"""Veiklų API: medis su kiekiais ir nusileidimo puslapio duomenys (docs/activities.md)."""

from __future__ import annotations

from django.db.models import Count
from ninja.errors import HttpError

from ..activities import activity_product_counts, activity_q, localized
from ..api_schemas import ActivityDetailOut, ActivityOut
from ..models import Activity, Brand, Category, Product
from .shared import (
    _shop_or_404_when_strict,
    apply_shop_product_scope,
    get_request_language_code,
    localize_brand,
    localize_category,
    router,
)


def _scoped_products(request):
    shop = _shop_or_404_when_strict(request)
    qs = Product.objects.filter(is_active=True)
    return apply_shop_product_scope(qs=qs, shop_id=int(shop.id) if shop else None)


def _out(a: Activity, counts: dict[int, int], language_code: str | None) -> dict:
    loc = localized(a, language_code)
    n = counts.get(a.id, 0)
    return {
        "id": a.id,
        "code": a.code,
        "name": loc["name"],
        "parent_code": a.parent.code if a.parent_id else None,
        "short_description": loc["short_description"],
        "image_url": a.image.url if a.image else None,
        "image_alt": a.image_alt,
        "product_count": n,
        "visible": n >= a.min_products,
        "children": [],
    }


@router.get("/activities", response=list[ActivityOut])
def activities(request, language_code: str | None = None, include_hidden: bool = False):
    """Sritys su veiklomis. `visible=false` – per mažai prekių (min_products);
    tokios grąžinamos tik su include_hidden=true."""
    if language_code is None:
        language_code = get_request_language_code(request)
    counts = activity_product_counts(_scoped_products(request))
    rows = list(
        Activity.objects.filter(is_active=True)
        .select_related("parent")
        .prefetch_related("translations")
    )
    roots = [_out(a, counts, language_code) for a in rows if not a.parent_id]
    by_code = {r["code"]: r for r in roots}
    for a in rows:
        if a.parent_id and a.parent.code in by_code and a.parent.is_active:
            by_code[a.parent.code]["children"].append(_out(a, counts, language_code))
    out = []
    for r in roots:
        if not include_hidden:
            r["children"] = [c for c in r["children"] if c["visible"]]
            if not r["visible"]:
                continue
        out.append(r)
    return out


@router.get("/activities/{code}", response=ActivityDetailOut)
def activity_detail(request, code: str, language_code: str | None = None):
    if language_code is None:
        language_code = get_request_language_code(request)
    a = (
        Activity.objects.filter(code=code, is_active=True)
        .select_related("parent")
        .prefetch_related("translations", "parent__translations")
        .first()
    )
    if not a or (a.parent_id and not a.parent.is_active):
        raise HttpError(404, "Activity not found")
    scoped = _scoped_products(request)
    counts = activity_product_counts(scoped)
    out = _out(a, counts, language_code)
    loc = localized(a, language_code)
    out.update(
        content_json=loc["content_json"],
        seo_title=loc["seo_title"],
        seo_description=loc["seo_description"],
        parent=_out(a.parent, counts, language_code) if a.parent_id else None,
    )
    if not a.parent_id:
        out["children"] = [
            _out(c, counts, language_code)
            for c in a.children.filter(is_active=True).prefetch_related("translations")
        ]
    members = scoped.filter(activity_q(a))
    cats = (
        members.exclude(category__isnull=True)
        .values("category_id").annotate(n=Count("id")).order_by("-n")[:24]
    )
    cat_map = {
        c.id: c
        for c in Category.objects.filter(
            id__in=[r["category_id"] for r in cats], is_active=True
        ).prefetch_related("translations")
    }
    out["top_categories"] = []
    for r in cats:
        c = cat_map.get(r["category_id"])
        if c:
            lc = localize_category(c, language_code=language_code)
            out["top_categories"].append(
                {"slug": lc.get("slug") or c.slug, "name": lc.get("name") or c.name, "product_count": r["n"]}
            )
    brands = (
        members.exclude(brand__isnull=True)
        .values("brand_id").annotate(n=Count("id")).order_by("-n")[:24]
    )
    b_map = {
        b.id: b
        for b in Brand.objects.filter(id__in=[r["brand_id"] for r in brands], is_active=True)
        .prefetch_related("translations")
    }
    out["top_brands"] = []
    for r in brands:
        b = b_map.get(r["brand_id"])
        if b:
            lb = localize_brand(b, language_code=language_code)
            out["top_brands"].append(
                {"slug": lb.get("slug") or b.slug, "name": lb.get("name") or b.name, "product_count": r["n"]}
            )
    return out
