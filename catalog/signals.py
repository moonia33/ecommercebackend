from __future__ import annotations

from django.db import transaction
from django.db.models import Q
from django.db.models.signals import post_save, pre_save
from django.dispatch import receiver
from django.utils import timezone

from notifications.services import send_templated_email

from .models import BackInStockSubscription, InventoryItem


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

    channel = "outlet" if instance.offer_visibility == InventoryItem.OfferVisibility.OUTLET else "normal"
    variant = instance.variant
    product = getattr(variant, "product", None)

    def _send():
        q = Q()
        if variant is not None:
            q |= Q(variant=variant)
        if product is not None:
            q |= Q(product=product)
        if not q:
            return

        qs = BackInStockSubscription.objects.filter(
            is_active=True,
            notified_at__isnull=True,
            channel=channel,
        ).filter(q)

        now = timezone.now()
        for sub in qs.distinct().iterator():
            product_name = getattr(product, "name", "") if product else ""
            variant_sku = getattr(variant, "sku", "") if variant else ""
            product_url = getattr(product, "get_absolute_url", lambda: "")()
            result = send_templated_email(
                template_key="catalog_back_in_stock",
                to_email=sub.email,
                context={
                    "product_name": product_name,
                    "variant_sku": variant_sku,
                    "product_url": product_url,
                },
                language_code=getattr(sub, "language_code", "") or None,
                site_id=int(getattr(sub, "site_id", 0) or 0) or None,
            )
            if result.ok:
                sub.notified_at = now
                sub.is_active = False
                sub.save(update_fields=["notified_at", "is_active"])

    transaction.on_commit(_send)
