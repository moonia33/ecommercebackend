from __future__ import annotations

from decimal import Decimal

from django.db import models


class Holiday(models.Model):
    date = models.DateField()
    country_code = models.CharField(max_length=2, default="LT")
    name = models.CharField(max_length=255, blank=True, default="")
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["country_code", "date"]
        constraints = [
            models.UniqueConstraint(fields=["country_code", "date"], name="uniq_holiday_country_date"),
        ]

    def __str__(self) -> str:
        return f"{self.country_code} {self.date}"


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


class DeliveryRule(models.Model):
    class Kind(models.TextChoices):
        LEAD_TIME = "lead_time", "Lead time"
        CYCLE = "cycle", "Cycle"

    code = models.SlugField(max_length=100, unique=True)
    name = models.CharField(max_length=255, blank=True, default="")
    is_active = models.BooleanField(default=True)
    priority = models.IntegerField(default=0)

    kind = models.CharField(max_length=20, choices=Kind.choices, default=Kind.LEAD_TIME)

    valid_from = models.DateField(null=True, blank=True)
    valid_to = models.DateField(null=True, blank=True)
    timezone = models.CharField(max_length=64, default="Europe/Vilnius")

    # Targeting
    channel = models.CharField(max_length=20, blank=True, default="")
    warehouse = models.ForeignKey(
        "catalog.Warehouse",
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="delivery_rules",
    )
    brand = models.ForeignKey(
        "catalog.Brand",
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="delivery_rules",
    )
    category = models.ForeignKey(
        "catalog.Category",
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="delivery_rules",
    )
    product_group = models.ForeignKey(
        "catalog.ProductGroup",
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="delivery_rules",
    )
    product = models.ForeignKey(
        "catalog.Product",
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="delivery_rules",
    )

    # Variant A: lead-time
    processing_business_days_min = models.PositiveSmallIntegerField(default=0)
    processing_business_days_max = models.PositiveSmallIntegerField(default=0)
    shipping_business_days_min = models.PositiveSmallIntegerField(default=0)
    shipping_business_days_max = models.PositiveSmallIntegerField(default=0)
    cutoff_time = models.TimeField(null=True, blank=True)

    # Variant B2: cycle-based dropship
    order_window_start_weekday = models.PositiveSmallIntegerField(null=True, blank=True)
    order_window_start_time = models.TimeField(null=True, blank=True)
    order_window_end_weekday = models.PositiveSmallIntegerField(null=True, blank=True)
    order_window_end_time = models.TimeField(null=True, blank=True)

    supplier_inbound_business_days_min = models.PositiveSmallIntegerField(default=0)
    supplier_inbound_business_days_max = models.PositiveSmallIntegerField(default=0)
    warehouse_pack_business_days_min = models.PositiveSmallIntegerField(default=0)
    warehouse_pack_business_days_max = models.PositiveSmallIntegerField(default=0)
    carrier_business_days_min = models.PositiveSmallIntegerField(default=0)
    carrier_business_days_max = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["-priority", "code"]

    def __str__(self) -> str:
        return self.code
