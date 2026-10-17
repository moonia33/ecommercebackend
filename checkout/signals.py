from __future__ import annotations

from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver


@receiver([post_save, post_delete], sender="checkout.OrderLine")
def orderline_fulfillment_eta(sender, instance, **kwargs):
    """Keep order fulfillment_mode + ETA in sync with its lines."""
    from checkout.services import (
        recompute_order_delivery_eta,
        recompute_order_fulfillment_mode,
    )

    order = instance.order
    recompute_order_fulfillment_mode(order=order)
    recompute_order_delivery_eta(order=order)
