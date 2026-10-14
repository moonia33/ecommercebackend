from __future__ import annotations

from django.db.models.signals import post_save, pre_save
from django.dispatch import receiver

from .models import InventoryItem


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
