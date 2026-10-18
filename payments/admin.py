from __future__ import annotations

from django.contrib import admin

from .models import PaymentMethod, ShopPaymentMethodPolicy


@admin.register(PaymentMethod)
class PaymentMethodAdmin(admin.ModelAdmin):
    list_display = (
        "code",
        "name",
        "kind",
        "provider",
        "image",
        "is_active",
        "country_code",
        "sort_order",
        "updated_at",
    )
    list_filter = ("is_active", "kind", "provider", "country_code")
    search_fields = ("code", "name")
    ordering = ("sort_order", "code")
    filter_horizontal = ("allowed_shipping_methods",)


@admin.register(ShopPaymentMethodPolicy)
class ShopPaymentMethodPolicyAdmin(admin.ModelAdmin):
    list_display = ("shop", "payment_method", "country_code", "is_active", "name_override", "sort_order_override")
    list_filter = ("shop", "is_active", "country_code")
    search_fields = ("shop__code", "payment_method__code", "payment_method__name")
    autocomplete_fields = ("shop", "payment_method")
