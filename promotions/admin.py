from __future__ import annotations

from django.contrib import admin

from .models import Coupon, PromoRule, PromoRuleCondition, PromoRuleConditionGroup


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
    filter_horizontal = ("free_shipping_methods",)
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


class PromoRuleConditionInline(admin.TabularInline):
    model = PromoRuleCondition
    extra = 0
    # FK select'ai su 20k+ produktų/31k variantų buvo pagrindinė
    # lėto group open/save priežastis.
    autocomplete_fields = (
        "category", "brand", "product", "variant",
        "product_group", "feature_value",
    )


class PromoRuleConditionGroupInline(admin.TabularInline):
    model = PromoRuleConditionGroup
    extra = 0
    show_change_link = True


@admin.register(PromoRule)
class PromoRuleAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "is_active",
        "priority",
        "percent_off",
        "amount_off_net_eur",
        "shop",
        "start_at",
        "end_at",
    )
    list_filter = ("is_active", "shop")
    search_fields = ("name",)
    filter_horizontal = ("customer_groups", "channels")
    readonly_fields = ("created_at", "updated_at")
    autocomplete_fields = ("category", "brand", "product", "variant", "shop")
    inlines = (PromoRuleConditionGroupInline,)
    fieldsets = (
        (None, {"fields": ("name", "is_active", "priority", "start_at", "end_at")}),
        (
            "Applies to",
            {
                "fields": (
                    "shop",
                    "channels",
                    "customer_groups",
                )
            },
        ),
        (
            "Scope (legacy)",
            {
                "fields": ("scope", "category", "brand", "product", "variant"),
                "classes": ("collapse",),
            },
        ),
        ("Discount", {"fields": ("percent_off", "amount_off_net_eur")}),
        ("Meta", {"fields": ("created_at", "updated_at")}),
    )


@admin.register(PromoRuleConditionGroup)
class PromoRuleConditionGroupAdmin(admin.ModelAdmin):
    list_display = ("id", "promo_rule", "name", "sort_order")
    search_fields = ("promo_rule__name", "name")
    autocomplete_fields = ("promo_rule",)
    inlines = (PromoRuleConditionInline,)
