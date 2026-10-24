from __future__ import annotations

from django.db.models.signals import post_delete, post_save, pre_save
from django.dispatch import receiver

from .models import (
    Brand,
    Category,
    CategoryFacet,
    Feature,
    FeatureValue,
    InventoryItem,
    OptionType,
    OptionValue,
    Product,
    ProductFeatureValue,
    ProductGroup,
    ProductImage,
    VariantFeatureValue,
    VariantOptionValue,
)


def _bump_catalog_categories_cache(**kwargs):
    """Kategorijų endpoint'o cache versija — naujos kategorijos, rikiavimas
    arba prekės→kategorija priskyrimas matomas iškart, ne per TTL."""
    from django.core.cache import cache
    try:
        cache.incr("catalog:categories:ver")
    except ValueError:
        cache.set("catalog:categories:ver", 2)


def _bump_catalog_facets_cache(**kwargs):
    """Facets endpoint'o cache versija — feature/option/facet konfigūracijos
    ir prekių priskyrimų pakeitimai matomi iškart, ne per TTL."""
    from django.core.cache import cache
    try:
        cache.incr("catalog:facets:ver")
    except ValueError:
        cache.set("catalog:facets:ver", 2)


for _sender in (Category, Product):
    post_save.connect(
        _bump_catalog_categories_cache, sender=_sender,
        dispatch_uid=f"catcache_save_{_sender.__name__}")
    post_delete.connect(
        _bump_catalog_categories_cache, sender=_sender,
        dispatch_uid=f"catcache_del_{_sender.__name__}")


for _sender in (
    Product, Brand, Category, CategoryFacet, Feature, FeatureValue,
    ProductFeatureValue, VariantFeatureValue, OptionType, OptionValue,
    VariantOptionValue, ProductGroup,
):
    post_save.connect(
        _bump_catalog_facets_cache, sender=_sender,
        dispatch_uid=f"facetcache_save_{_sender.__name__}")
    post_delete.connect(
        _bump_catalog_facets_cache, sender=_sender,
        dispatch_uid=f"facetcache_del_{_sender.__name__}")


@receiver(pre_save, sender=InventoryItem)
def inventory_item_pre_save(sender, instance: InventoryItem, **kwargs):
    prev_available = 0
    if instance.pk:
        prev = InventoryItem.objects.filter(pk=instance.pk).first()
        if prev is not None:
            prev_available = int(prev.qty_available)
    instance._prev_qty_available = prev_available


@receiver(post_save, sender=InventoryItem)
def inventory_item_post_save(sender, instance: InventoryItem, created: bool, **kwargs):
    prev_available = int(getattr(instance, "_prev_qty_available", 0))
    new_available = int(instance.qty_available)

    if prev_available > 0 or new_available <= 0:
        return

    from django.db import transaction

    pk = instance.pk

    def _dispatch(pk=pk):
        from catalog.tasks import notify_back_in_stock_task
        from config.celery import safe_delay
        safe_delay(notify_back_in_stock_task, pk)

    transaction.on_commit(_dispatch)


@receiver(post_delete, sender=ProductImage)
def product_image_post_delete(sender, instance: ProductImage, **kwargs):
    """Remove files from object storage — Django does not do this on delete."""
    for field in (
        "image",
        "image_avif",
        "image_webp",
        "listing_avif",
        "listing_webp",
        "listing_avif_2x",
        "listing_webp_2x",
        "detail_avif",
        "detail_webp",
    ):
        f = getattr(instance, field, None)
        if f and getattr(f, "name", ""):
            try:
                f.storage.delete(f.name)
            except Exception:
                pass


@receiver(pre_save, sender=Brand)
def brand_pre_save(sender, instance: Brand, **kwargs):
    prev = ""
    if instance.pk:
        prev = (
            Brand.objects.filter(pk=instance.pk)
            .values_list("logo", flat=True)
            .first()
            or ""
        )
    instance._prev_logo_name = prev


@receiver(post_save, sender=Brand)
def brand_post_save(sender, instance: Brand, **kwargs):
    """Brand logo thumb (256px webp) — deterministic name, overwritten."""
    logo_name = instance.logo.name if instance.logo else ""
    thumb_name = instance.logo_thumb.name if instance.logo_thumb else ""

    if not logo_name:
        if thumb_name:
            try:
                instance.logo_thumb.storage.delete(thumb_name)
            except Exception:
                pass
            Brand.objects.filter(pk=instance.pk).update(logo_thumb=None)
        return

    if thumb_name and logo_name == getattr(instance, "_prev_logo_name", ""):
        return

    try:
        from io import BytesIO

        from django.core.files.base import ContentFile
        from PIL import Image

        instance.logo.open()
        img = Image.open(instance.logo)
        if img.mode != "RGBA":
            img = img.convert("RGBA")
        edge = 256
        if max(img.size) > edge:
            img.thumbnail((edge, edge))
        buf = BytesIO()
        img.save(buf, format="WEBP", quality=80, method=6)
        buf.seek(0)

        name = f"brand-logos/thumbs/brand_{instance.pk}.webp"
        storage = instance.logo_thumb.storage
        if storage.exists(name):
            storage.delete(name)
        storage.save(name, ContentFile(buf.read()))
        Brand.objects.filter(pk=instance.pk).update(logo_thumb=name)
    except Exception:
        # SVG ir kiti PIL nepalaikomi formatai — be thumb'o, fallback
        # i originala (logo_thumb_url_resolved).
        pass


@receiver(post_delete, sender=Brand)
def brand_post_delete(sender, instance: Brand, **kwargs):
    for field in ("logo", "logo_thumb"):
        f = getattr(instance, field, None)
        if f and getattr(f, "name", ""):
            try:
                f.storage.delete(f.name)
            except Exception:
                pass
