from __future__ import annotations

from django.db import models

from .product import Product
from .taxonomy import Brand, Category, ProductGroup
from .variants import Variant


class ShopAssortmentRule(models.Model):
    class Scope(models.TextChoices):
        INCLUDE = "include", "Include"
        EXCLUDE = "exclude", "Exclude"

    shop = models.ForeignKey("api.Shop", on_delete=models.CASCADE, related_name="assortment_rules")
    scope = models.CharField(max_length=10, choices=Scope.choices, default=Scope.INCLUDE)
    priority = models.IntegerField(default=0, help_text="Higher priority is evaluated first.")
    is_active = models.BooleanField(default=True)

    category = models.ForeignKey(Category, null=True, blank=True, on_delete=models.CASCADE, related_name="shop_assortment_rules")
    brand = models.ForeignKey(Brand, null=True, blank=True, on_delete=models.CASCADE, related_name="shop_assortment_rules")
    product_group = models.ForeignKey(ProductGroup, null=True, blank=True, on_delete=models.CASCADE, related_name="shop_assortment_rules")
    product = models.ForeignKey(Product, null=True, blank=True, on_delete=models.CASCADE, related_name="shop_assortment_rules")

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["shop_id", "-priority", "id"]
        indexes = [
            models.Index(fields=["shop", "is_active", "scope"]),
            models.Index(fields=["shop", "product"]),
            models.Index(fields=["shop", "category"]),
            models.Index(fields=["shop", "brand"]),
            models.Index(fields=["shop", "product_group"]),
        ]

    def __str__(self) -> str:
        return f"{self.shop.code}:{self.scope}:{self.id}"


class ShopPriceList(models.Model):
    class ChannelType(models.TextChoices):
        RETAIL = "retail", "Retail"
        B2B = "b2b", "B2B"

    shop = models.ForeignKey("api.Shop", on_delete=models.CASCADE, related_name="price_lists")
    code = models.SlugField(max_length=64)
    name = models.CharField(max_length=200)
    channel_type = models.CharField(max_length=16, choices=ChannelType.choices, default=ChannelType.RETAIL)
    customer_group = models.ForeignKey(
        "accounts.CustomerGroup",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="shop_price_lists",
    )
    priority = models.IntegerField(default=0, help_text="Higher value wins.")
    is_active = models.BooleanField(default=True)

    valid_from = models.DateField(null=True, blank=True)
    valid_to = models.DateField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["shop_id", "-priority", "code"]
        constraints = [
            models.UniqueConstraint(fields=["shop", "code"], name="uniq_shop_price_list_code"),
        ]
        indexes = [
            models.Index(fields=["shop", "is_active", "channel_type", "priority"]),
            models.Index(fields=["shop", "customer_group", "is_active"]),
        ]

    def __str__(self) -> str:
        return f"{self.shop.code}:{self.code}"


class ShopPriceRule(models.Model):
    class RuleType(models.TextChoices):
        FIXED_PRICE = "fixed_price", "Fixed price"
        PERCENT_DISCOUNT = "percent_discount", "Percent discount"
        DELTA = "delta", "Delta amount"

    price_list = models.ForeignKey(ShopPriceList, on_delete=models.CASCADE, related_name="rules")
    is_active = models.BooleanField(default=True)
    priority = models.IntegerField(default=0, help_text="Higher value wins.")

    product = models.ForeignKey(Product, null=True, blank=True, on_delete=models.CASCADE, related_name="shop_price_rules")
    variant = models.ForeignKey(Variant, null=True, blank=True, on_delete=models.CASCADE, related_name="shop_price_rules")
    category = models.ForeignKey(Category, null=True, blank=True, on_delete=models.CASCADE, related_name="shop_price_rules")
    brand = models.ForeignKey(Brand, null=True, blank=True, on_delete=models.CASCADE, related_name="shop_price_rules")
    product_group = models.ForeignKey(ProductGroup, null=True, blank=True, on_delete=models.CASCADE, related_name="shop_price_rules")

    rule_type = models.CharField(max_length=32, choices=RuleType.choices, default=RuleType.PERCENT_DISCOUNT)
    amount_eur = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    percent = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True)
    min_price_eur = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["price_list_id", "-priority", "id"]
        indexes = [
            models.Index(fields=["price_list", "is_active", "priority"]),
            models.Index(fields=["price_list", "product"]),
            models.Index(fields=["price_list", "variant"]),
        ]

    def __str__(self) -> str:
        return f"{self.price_list.code}:{self.id}"
