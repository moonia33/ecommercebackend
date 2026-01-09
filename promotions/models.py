from __future__ import annotations

from decimal import Decimal

from django.db import models


class Coupon(models.Model):
    code = models.SlugField(max_length=40, unique=True)
    name = models.CharField(max_length=150, blank=True, default="")

    # Discount configuration (applies to items/cart only)
    percent_off = models.PositiveSmallIntegerField(null=True, blank=True)
    amount_off_net_eur = models.DecimalField(
        max_digits=12, decimal_places=2, null=True, blank=True
    )
    apply_on_discounted_items = models.BooleanField(default=False)

    # Free shipping (does not affect fees)
    free_shipping = models.BooleanField(default=False)
    free_shipping_methods_json = models.JSONField(default=list, blank=True)
    free_shipping_methods = models.ManyToManyField(
        "shipping.ShippingMethod",
        blank=True,
        related_name="coupons_free_shipping",
        help_text="If empty and free_shipping=true, free shipping applies to all methods.",
    )

    # Usage limits (count only for PAID orders)
    usage_limit_total = models.PositiveIntegerField(null=True, blank=True)
    usage_limit_per_user = models.PositiveIntegerField(null=True, blank=True)
    times_redeemed = models.PositiveIntegerField(default=0)

    is_active = models.BooleanField(default=True)

    start_at = models.DateTimeField(null=True, blank=True)
    end_at = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["code"]

    def __str__(self) -> str:
        return self.code

    def is_valid_now(self, *, now=None) -> bool:
        from django.utils import timezone

        now = now or timezone.now()
        if not self.is_active:
            return False
        if self.start_at and now < self.start_at:
            return False
        if self.end_at and now > self.end_at:
            return False
        return True

    def get_discount_net_for(self, *, eligible_items_net: Decimal) -> Decimal:
        eligible_items_net = Decimal(eligible_items_net or 0)
        if eligible_items_net <= 0:
            return Decimal("0.00")

        if self.percent_off is not None:
            pct = int(self.percent_off)
            pct = max(0, min(100, pct))
            return (eligible_items_net * (Decimal(pct) / Decimal(100))).quantize(Decimal("0.01"))

        if self.amount_off_net_eur is not None:
            amt = Decimal(self.amount_off_net_eur)
            if amt <= 0:
                return Decimal("0.00")
            return min(eligible_items_net, amt).quantize(Decimal("0.01"))

        return Decimal("0.00")

    def is_free_shipping_for(self, *, shipping_method: str) -> bool:
        if not self.free_shipping:
            return False
        m = (shipping_method or "").strip()

        # Prefer relational config.
        allowed_qs = self.free_shipping_methods.all()
        if allowed_qs.exists():
            return allowed_qs.filter(code=m).exists()

        # Backward-compatible fallback for legacy JSON.
        allowed = list(self.free_shipping_methods_json or [])
        if not allowed:
            return True
        return m in allowed


class CouponRedemption(models.Model):
    coupon = models.ForeignKey(Coupon, on_delete=models.CASCADE, related_name="redemptions")
    user = models.ForeignKey(
        "accounts.User",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="coupon_redemptions",
    )
    order = models.OneToOneField(
        "checkout.Order",
        on_delete=models.CASCADE,
        related_name="coupon_redemption",
    )

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["id"]
        indexes = [
            models.Index(fields=["coupon", "user"]),
            models.Index(fields=["coupon", "-created_at"]),
        ]
        constraints = [
            models.UniqueConstraint(fields=["coupon", "order"], name="uniq_coupon_redemption_coupon_order"),
        ]

    def __str__(self) -> str:
        return f"coupon:{self.coupon_id} order:{self.order_id} user:{self.user_id}"


class SalesChannel(models.Model):
    code = models.SlugField(max_length=40, unique=True)
    name = models.CharField(max_length=120)
    is_active = models.BooleanField(default=True)
    sort_order = models.IntegerField(default=0)

    class Meta:
        ordering = ["sort_order", "code"]

    def __str__(self) -> str:
        return self.code


class PromoRule(models.Model):
    class Scope(models.TextChoices):
        ALL = "all", "All"
        CATEGORY = "category", "Category"
        BRAND = "brand", "Brand"
        PRODUCT = "product", "Product"
        VARIANT = "variant", "Variant"

    name = models.CharField(max_length=200)
    is_active = models.BooleanField(default=True)
    priority = models.IntegerField(default=0, help_text="Higher wins")

    start_at = models.DateTimeField(null=True, blank=True)
    end_at = models.DateTimeField(null=True, blank=True)

    # e.g. ["normal"], ["outlet"], ["normal","outlet"]
    allowed_channels_json = models.JSONField(default=list, blank=True)
    channels = models.ManyToManyField(
        SalesChannel,
        blank=True,
        related_name="promo_rules",
        help_text="If empty, applies to all channels.",
    )

    # Discount configuration (net)
    percent_off = models.PositiveSmallIntegerField(null=True, blank=True)
    amount_off_net_eur = models.DecimalField(
        max_digits=12, decimal_places=2, null=True, blank=True
    )

    scope = models.CharField(max_length=20, choices=Scope.choices, default=Scope.ALL)
    category = models.ForeignKey(
        "catalog.Category",
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="promo_rules",
    )
    brand = models.ForeignKey(
        "catalog.Brand",
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="promo_rules",
    )
    product = models.ForeignKey(
        "catalog.Product",
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="promo_rules",
    )
    variant = models.ForeignKey(
        "catalog.Variant",
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="promo_rules",
    )

    customer_groups = models.ManyToManyField(
        "accounts.CustomerGroup",
        blank=True,
        related_name="promo_rules",
        help_text="If empty, applies to all customers. If set, applies only to users in these groups.",
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-priority", "name", "id"]
        indexes = [
            models.Index(fields=["is_active", "-priority"]),
            models.Index(fields=["scope", "is_active"]),
        ]

    def __str__(self) -> str:
        return self.name

    def is_valid_now(self, *, now=None) -> bool:
        from django.utils import timezone

        now = now or timezone.now()
        if not self.is_active:
            return False
        if self.start_at and now < self.start_at:
            return False
        if self.end_at and now > self.end_at:
            return False
        return True

    def allows_channel(self, *, channel: str) -> bool:
        ch = (channel or "").strip().lower() or "normal"

        # Prefer relational config.
        qs = self.channels.all()
        if qs.exists():
            return qs.filter(code=ch).exists()

        # Backward-compatible fallback for legacy JSON.
        allowed = [
            str(c).strip().lower()
            for c in (self.allowed_channels_json or [])
            if str(c).strip()
        ]
        if not allowed:
            return True
        return ch in set(allowed)

    def get_discount_net_for(self, *, eligible_unit_net: Decimal) -> Decimal:
        eligible_unit_net = Decimal(eligible_unit_net or 0)
        if eligible_unit_net <= 0:
            return Decimal("0.00")

        if self.percent_off is not None:
            pct = int(self.percent_off)
            pct = max(0, min(100, pct))
            return (eligible_unit_net * (Decimal(pct) / Decimal(100))).quantize(Decimal("0.01"))

        if self.amount_off_net_eur is not None:
            amt = Decimal(self.amount_off_net_eur)
            if amt <= 0:
                return Decimal("0.00")
            return min(eligible_unit_net, amt).quantize(Decimal("0.01"))

        return Decimal("0.00")


class PromoRuleConditionGroup(models.Model):
    promo_rule = models.ForeignKey(
        PromoRule, on_delete=models.CASCADE, related_name="condition_groups"
    )
    name = models.CharField(max_length=120, blank=True, default="")
    sort_order = models.IntegerField(default=0)

    class Meta:
        ordering = ["sort_order", "id"]

    def __str__(self) -> str:
        return self.name or f"Group {self.id}"


class PromoRuleCondition(models.Model):
    class Kind(models.TextChoices):
        CATEGORY = "category", "Category"
        BRAND = "brand", "Brand"
        PRODUCT = "product", "Product"
        VARIANT = "variant", "Variant"
        PRODUCT_GROUP = "product_group", "Product group"
        FEATURE_VALUE = "feature_value", "Feature value"

    group = models.ForeignKey(
        PromoRuleConditionGroup, on_delete=models.CASCADE, related_name="conditions"
    )
    kind = models.CharField(max_length=30, choices=Kind.choices)

    category = models.ForeignKey(
        "catalog.Category",
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="promo_rule_conditions",
    )
    brand = models.ForeignKey(
        "catalog.Brand",
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="promo_rule_conditions",
    )
    product = models.ForeignKey(
        "catalog.Product",
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="promo_rule_conditions",
    )
    variant = models.ForeignKey(
        "catalog.Variant",
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="promo_rule_conditions",
    )
    product_group = models.ForeignKey(
        "catalog.ProductGroup",
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="promo_rule_conditions",
    )
    feature_value = models.ForeignKey(
        "catalog.FeatureValue",
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="promo_rule_conditions",
    )

    class Meta:
        ordering = ["id"]
        indexes = [
            models.Index(fields=["kind"]),
        ]

    def __str__(self) -> str:
        return f"{self.kind}:{self.id}"
