from __future__ import annotations

import threading
from contextlib import contextmanager

from django.db import transaction
from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from catalog.models import (
    FeatureValue,
    InventoryItem,
    Product,
    ProductFeatureValue,
    ProductTranslation,
    ShopAssortmentRule,
    Variant,
    VariantFeatureValue,
)


_state = threading.local()


@contextmanager
def suppress_search_sync():
    """Mute per-row index sync (bulk imports re-sync once at the end)."""
    prev = getattr(_state, "suppressed", False)
    _state.suppressed = True
    try:
        yield
    finally:
        _state.suppressed = prev


def search_sync_suppressed() -> bool:
    return getattr(_state, "suppressed", False)


def _enqueue(product_id: int | None):
    if not product_id or search_sync_suppressed():
        return
    from .services import search_enabled
    if not search_enabled():
        return
    pk = int(product_id)

    def _dispatch(pk=pk):
        from config.celery import safe_delay
        from .tasks import sync_product_document_task
        safe_delay(sync_product_document_task, pk)

    transaction.on_commit(_dispatch)


def _enqueue_delete(product_id: int | None):
    if not product_id or search_sync_suppressed():
        return
    from .services import search_enabled
    if not search_enabled():
        return
    pk = int(product_id)

    def _dispatch(pk=pk):
        from config.celery import safe_delay
        from .tasks import delete_product_document_task
        safe_delay(delete_product_document_task, pk)

    transaction.on_commit(_dispatch)


@receiver(post_save, sender=Product)
def product_saved(sender, instance: Product, **kwargs):
    _enqueue(instance.id)


@receiver(post_delete, sender=Product)
def product_deleted(sender, instance: Product, **kwargs):
    _enqueue_delete(instance.id)


@receiver(post_save, sender=Variant)
def variant_saved(sender, instance: Variant, **kwargs):
    _enqueue(instance.product_id)


@receiver(post_delete, sender=Variant)
def variant_deleted(sender, instance: Variant, **kwargs):
    _enqueue(instance.product_id)


@receiver(post_save, sender=InventoryItem)
def inventory_item_saved(sender, instance: InventoryItem, **kwargs):
    _enqueue(instance.variant.product_id if instance.variant_id else None)


@receiver(post_delete, sender=InventoryItem)
def inventory_item_deleted(sender, instance: InventoryItem, **kwargs):
    _enqueue(instance.variant.product_id if instance.variant_id else None)


@receiver(post_save, sender=ProductFeatureValue)
def product_feature_saved(sender, instance: ProductFeatureValue, **kwargs):
    _enqueue(instance.product_id)


@receiver(post_delete, sender=ProductFeatureValue)
def product_feature_deleted(sender, instance: ProductFeatureValue, **kwargs):
    _enqueue(instance.product_id)


@receiver(post_save, sender=ProductTranslation)
def product_translation_saved(sender, instance: ProductTranslation, **kwargs):
    _enqueue(instance.product_id)


@receiver(post_save, sender=VariantFeatureValue)
def variant_feature_saved(sender, instance: VariantFeatureValue, **kwargs):
    _enqueue(instance.variant.product_id if instance.variant_id else None)


@receiver(post_delete, sender=VariantFeatureValue)
def variant_feature_deleted(sender, instance: VariantFeatureValue, **kwargs):
    _enqueue(instance.variant.product_id if instance.variant_id else None)


@receiver(post_save, sender=FeatureValue)
def feature_value_saved(sender, instance: FeatureValue, **kwargs):
    """Renamed feature values must refresh every product using them."""
    if search_sync_suppressed():
        return
    product_ids = list(
        instance.product_values.values_list("product_id", flat=True)[:1000]
    )
    if not product_ids:
        return
    from .services import search_enabled
    if not search_enabled():
        return

    def _dispatch(ids=tuple(product_ids)):
        from config.celery import safe_delay
        from .tasks import sync_products_task
        safe_delay(sync_products_task, list(ids))

    transaction.on_commit(_dispatch)


@receiver(post_save, sender=ShopAssortmentRule)
@receiver(post_delete, sender=ShopAssortmentRule)
def assortment_rule_changed(sender, instance: ShopAssortmentRule, **kwargs):
    """Shop scope changes can flip shop_ids on many products."""
    from .services import search_enabled
    if not search_enabled():
        return

    def _dispatch():
        from config.celery import safe_delay
        from .tasks import reindex_products_task
        safe_delay(reindex_products_task)

    transaction.on_commit(_dispatch)


def _schedule_curation_sync():
    from .services import search_enabled
    if not search_enabled():
        return

    def _dispatch():
        from config.celery import safe_delay
        from .tasks import sync_search_curation_task
        safe_delay(sync_search_curation_task)

    transaction.on_commit(_dispatch)


from .models import (  # noqa: E402
    SearchOverride,
    SearchOverrideExclude,
    SearchOverridePin,
    SearchSynonym,
)


@receiver(post_save, sender=SearchSynonym)
@receiver(post_delete, sender=SearchSynonym)
@receiver(post_save, sender=SearchOverride)
@receiver(post_delete, sender=SearchOverride)
@receiver(post_save, sender=SearchOverridePin)
@receiver(post_delete, sender=SearchOverridePin)
@receiver(post_save, sender=SearchOverrideExclude)
@receiver(post_delete, sender=SearchOverrideExclude)
def curation_changed(sender, **kwargs):
    _schedule_curation_sync()
