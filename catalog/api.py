from __future__ import annotations

from decimal import Decimal

from django.db.models import Min, Q
from ninja import Router
from ninja.errors import HttpError
from ninja.pagination import PageNumberPagination, paginate

from pricing.services import compute_vat, get_vat_rate

from .api_schemas import (
    BrandOut,
    BrandRefOut,
    CategoryOut,
    CategoryDetailOut,
    CategoryRefOut,
    MoneyOut,
    ProductDetailOut,
    ProductImageOut,
    ProductListOut,
    VariantOptionOut,
    VariantOut,
)
from .models import Brand, Category, Product, Variant

router = Router(tags=["catalog"])


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


class ProductPagination(PageNumberPagination):
    page_size = 20
    max_page_size = 100


@router.get("/categories", response=list[CategoryOut])
def categories(request):
    qs = Category.objects.filter(is_active=True).order_by("name")
    return [
        {
            "id": c.id,
            "slug": c.slug,
            "name": c.name,
            "parent_id": c.parent_id,
            "description": c.description or "",
            "hero_image_url": (c.hero_url or None),
            "menu_icon_url": (c.menu_icon_url_resolved or None),
            "seo_title": getattr(c, "seo_title", "") or "",
            "seo_description": getattr(c, "seo_description", "") or "",
            "seo_keywords": getattr(c, "seo_keywords", "") or "",
        }
        for c in qs
    ]


@router.get("/categories/{slug}", response=CategoryDetailOut)
def category_detail(request, slug: str):
    c = Category.objects.filter(
        slug=slug, is_active=True).select_related("parent").first()
    if not c:
        raise HttpError(404, "Category not found")
    return {
        "id": c.id,
        "slug": c.slug,
        "name": c.name,
        "parent_id": c.parent_id,
        "description": c.description or "",
        "hero_image_url": (c.hero_url or None),
        "menu_icon_url": (c.menu_icon_url_resolved or None),
        "seo_title": getattr(c, "seo_title", "") or "",
        "seo_description": getattr(c, "seo_description", "") or "",
        "seo_keywords": getattr(c, "seo_keywords", "") or "",
    }


@router.get("/brands", response=list[BrandOut])
def brands(request):
    qs = Brand.objects.filter(is_active=True).order_by("name")
    return [{"id": b.id, "slug": b.slug, "name": b.name} for b in qs]


@router.get("/brands/{slug}", response=BrandOut)
def brand_detail(request, slug: str):
    b = Brand.objects.filter(slug=slug, is_active=True).first()
    if not b:
        raise HttpError(404, "Brand not found")
    return {"id": b.id, "slug": b.slug, "name": b.name}


@router.get("/products", response=list[ProductListOut])
@paginate(ProductPagination)
def products(request, country_code: str = "LT"):
    country_code = (country_code or "").strip().upper()
    if len(country_code) != 2:
        raise HttpError(400, "Invalid country_code")

    # Representative list price: min active variant price (net) per product.
    min_price_expr = Min("variants__price_eur",
                         filter=Q(variants__is_active=True))

    qs = (
        Product.objects.filter(is_active=True)
        .select_related("brand", "category", "tax_class")
        .prefetch_related("images")
        .annotate(_min_variant_price=min_price_expr)
        .order_by("name", "id")
    )

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
    for p in qs:
        net = p._min_variant_price if p._min_variant_price is not None else 0
        rate = vat_rate_for(p)

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
                "price": _money_out(currency="EUR", unit_net=Decimal(net), vat_rate=rate),
            }
        )

    return out


@router.get("/products/{slug}", response=ProductDetailOut)
def product_detail(request, slug: str, country_code: str = "LT"):
    country_code = (country_code or "").strip().upper()
    if len(country_code) != 2:
        raise HttpError(400, "Invalid country_code")

    product = (
        Product.objects.filter(slug=slug, is_active=True)
        .select_related("brand", "category", "tax_class")
        .prefetch_related(
            "images",
            "variants",
            "variants__option_values__option_type",
            "variants__option_values__option_value",
            "variants__inventory_items",
        )
        .first()
    )
    if not product:
        raise HttpError(404, "Product not found")

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
    for v in variants_qs:
        # Stock: sum across inventory items.
        inv = list(v.inventory_items.all())
        if inv:
            stock = sum([ii.qty_available for ii in inv])
        else:
            stock = 0

        options = list(v.option_values.select_related(
            "option_type", "option_value").all())
        options.sort(key=lambda r: (
            r.option_type.sort_order, r.option_type.code))

        variants.append(
            {
                "id": v.id,
                "sku": v.sku,
                "barcode": v.barcode,
                "name": v.name,
                "is_active": bool(v.is_active),
                "stock_available": int(stock),
                "price": _money_out(currency="EUR", unit_net=Decimal(v.price_eur), vat_rate=Decimal(vat_rate)),
                "options": [
                    {
                        "option_type_code": r.option_type.code,
                        "option_type_name": r.option_type.name,
                        "option_value_code": r.option_value.code,
                        "option_value_label": r.option_value.label,
                    }
                    for r in options
                ],
            }
        )

    return {
        "id": product.id,
        "sku": product.sku,
        "slug": product.slug,
        "name": product.name,
        "description": product.description,
        "is_active": bool(product.is_active),
        "seo_title": getattr(product, "seo_title", "") or "",
        "seo_description": getattr(product, "seo_description", "") or "",
        "seo_keywords": getattr(product, "seo_keywords", "") or "",
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
        "variants": variants,
    }
