from __future__ import annotations

from django.db import models

from .features import OptionType, OptionValue
from .product import Product


class ProductOptionType(models.Model):
    product = models.ForeignKey(
        Product, on_delete=models.CASCADE, related_name="option_types")
    option_type = models.ForeignKey(
        OptionType, on_delete=models.PROTECT, related_name="products")
    sort_order = models.IntegerField(default=0)

    class Meta:
        ordering = ["sort_order", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["product", "option_type"],
                name="uniq_product_option_type",
            )
        ]

    def __str__(self) -> str:
        return f"{self.product_id}:{self.option_type.code}"


class Variant(models.Model):
    external_presta_combination_id = models.CharField(
        max_length=32,
        null=True,
        blank=True,
        unique=True,
        db_index=True,
        help_text="Original PrestaShop id_product_attribute (combination).",
    )
    product = models.ForeignKey(
        Product, on_delete=models.CASCADE, related_name="variants")
    sku = models.CharField(max_length=64, unique=True)
    barcode = models.CharField(max_length=64, blank=True)
    name = models.CharField(max_length=255, blank=True)

    # MVP: EUR-only. Price is net (excl VAT).
    price_eur = models.DecimalField(max_digits=12, decimal_places=2)
    cost_eur = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        null=True,
        blank=True,
        help_text="Purchase cost (net) for margin/discount floor checks.",
    )
    is_active = models.BooleanField(default=True)

    # Shipping data (per-variant)
    weight_g = models.PositiveIntegerField(default=0)
    length_cm = models.PositiveIntegerField(default=0)
    width_cm = models.PositiveIntegerField(default=0)
    height_cm = models.PositiveIntegerField(default=0)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["sku"]

    def __str__(self) -> str:
        return self.sku


class VariantOptionValue(models.Model):
    variant = models.ForeignKey(
        Variant, on_delete=models.CASCADE, related_name="option_values")
    option_type = models.ForeignKey(OptionType, on_delete=models.PROTECT)
    option_value = models.ForeignKey(OptionValue, on_delete=models.PROTECT)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["variant", "option_type"],
                name="uniq_variant_option_type",
            )
        ]

    def __str__(self) -> str:
        return f"{self.variant_id}:{self.option_type.code}={self.option_value.label}"
