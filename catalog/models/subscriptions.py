from __future__ import annotations

from django.db import models

from .product import Product


class BackInStockSubscription(models.Model):
    class Channel(models.TextChoices):
        NORMAL = "normal", "Normal"
        OUTLET = "outlet", "Outlet"

    email = models.EmailField()
    product = models.ForeignKey(
        Product,
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="back_in_stock_subscriptions",
    )
    variant = models.ForeignKey(
        "Variant",
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="back_in_stock_subscriptions",
    )
    channel = models.CharField(
        max_length=20,
        choices=Channel.choices,
        default=Channel.NORMAL,
    )
    language_code = models.CharField(max_length=8, blank=True, default="")
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    notified_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        constraints = [
            models.UniqueConstraint(
                fields=["email", "product", "variant", "channel"],
                name="uniq_back_in_stock_subscription",
            )
        ]

    def __str__(self) -> str:
        target = ""
        if self.variant_id:
            target = f"variant:{self.variant_id}"
        elif self.product_id:
            target = f"product:{self.product_id}"
        return f"{self.email} {self.channel} {target}".strip()
