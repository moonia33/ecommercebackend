from __future__ import annotations

from decimal import Decimal

from django.db import models


class ShippingMethod(models.Model):
    code = models.SlugField(max_length=50, unique=True)
    name = models.CharField(max_length=200)

    # e.g. "dpd", "lpexpress"
    carrier_code = models.CharField(max_length=32, blank=True, default="")

    requires_pickup_point = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)
    sort_order = models.IntegerField(default=0)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["sort_order", "code"]

    def __str__(self) -> str:
        return f"{self.code} - {self.name}"


class ShippingRate(models.Model):
    method = models.ForeignKey(
        ShippingMethod, on_delete=models.CASCADE, related_name="rates"
    )
    # ISO 3166-1 alpha-2
    country_code = models.CharField(max_length=2, default="LT")

    # MVP: EUR-only, net (excl VAT)
    net_eur = models.DecimalField(
        max_digits=12, decimal_places=2, default=Decimal("0.00"))

    is_active = models.BooleanField(default=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["method__sort_order", "method__code", "country_code"]
        constraints = [
            models.UniqueConstraint(
                fields=["method", "country_code"], name="uniq_shipping_rate_per_country"
            )
        ]

    def __str__(self) -> str:
        return f"{self.method.code} {self.country_code}: {self.net_eur} EUR net"
