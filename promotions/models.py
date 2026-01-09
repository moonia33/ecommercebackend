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
    free_shipping_methods = models.JSONField(default=list, blank=True)

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
        allowed = list(self.free_shipping_methods or [])
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
