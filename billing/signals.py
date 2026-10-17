from __future__ import annotations

from django.db import transaction
from django.db.models.signals import post_save, pre_save
from django.dispatch import receiver
from django.utils import timezone

from checkout.models import Order


@receiver(pre_save, sender=Order)
def order_delivery_pre_save(sender, instance: Order, **kwargs):
    """Track delivery_status transitions and anchor delivered_at.

    delivered_at marks when the customer took possession (DELIVERED covers
    courier delivery, locker pickup and store pickup) — it starts the
    return window and triggers invoicing.
    """
    instance._prev_delivery_status = None
    if instance.pk:
        prev = (
            Order.objects.filter(pk=instance.pk)
            .values_list("delivery_status", flat=True)
            .first()
        )
        instance._prev_delivery_status = prev

    new = instance.delivery_status
    if (
        new == Order.DeliveryStatus.DELIVERED
        and instance._prev_delivery_status != Order.DeliveryStatus.DELIVERED
        and instance.delivered_at is None
    ):
        instance.delivered_at = timezone.now()


@receiver(post_save, sender=Order)
def order_delivery_post_save(sender, instance: Order, **kwargs):
    """Issue the sales invoice once the order reaches DELIVERED."""
    if instance.delivery_status != Order.DeliveryStatus.DELIVERED:
        return
    if getattr(instance, "_prev_delivery_status", None) == Order.DeliveryStatus.DELIVERED:
        return

    order_id = instance.pk

    def _dispatch():
        from config.celery import safe_delay

        from .tasks import issue_invoice_task
        safe_delay(issue_invoice_task, order_id)

    transaction.on_commit(_dispatch)
