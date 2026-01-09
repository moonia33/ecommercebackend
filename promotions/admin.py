from __future__ import annotations

from django.contrib import admin

from .models import Coupon


@admin.register(Coupon)
class CouponAdmin(admin.ModelAdmin):
    list_display = (
        "code",
        "name",
        "is_active",
        "percent_off",
        "amount_off_net_eur",
        "apply_on_discounted_items",
        "free_shipping",
        "usage_limit_total",
        "usage_limit_per_user",
        "times_redeemed",
        "start_at",
        "end_at",
    )
    search_fields = ("code", "name")
    list_filter = ("is_active", "apply_on_discounted_items", "free_shipping")
    readonly_fields = ("times_redeemed", "created_at", "updated_at")
    fieldsets = (
        (None, {"fields": ("code", "name", "is_active", "start_at", "end_at")}),
        (
            "Discount",
            {
                "fields": (
                    "percent_off",
                    "amount_off_net_eur",
                    "apply_on_discounted_items",
                )
            },
        ),
        (
            "Free shipping",
            {
                "fields": (
                    "free_shipping",
                    "free_shipping_methods",
                )
            },
        ),
        (
            "Usage limits",
            {
                "fields": (
                    "usage_limit_total",
                    "usage_limit_per_user",
                    "times_redeemed",
                )
            },
        ),
        ("Meta", {"fields": ("created_at", "updated_at")}),
    )
