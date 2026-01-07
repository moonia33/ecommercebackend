from __future__ import annotations

from django.contrib import admin

from .models import ShippingMethod, ShippingRate


class ShippingRateInline(admin.TabularInline):
    model = ShippingRate
    extra = 0


@admin.register(ShippingMethod)
class ShippingMethodAdmin(admin.ModelAdmin):
    list_display = (
        "code",
        "name",
        "carrier_code",
        "requires_pickup_point",
        "is_active",
        "sort_order",
    )
    list_filter = ("is_active", "carrier_code", "requires_pickup_point")
    search_fields = ("code", "name", "carrier_code")
    ordering = ("sort_order", "code")
    inlines = (ShippingRateInline,)


@admin.register(ShippingRate)
class ShippingRateAdmin(admin.ModelAdmin):
    list_display = ("method", "country_code", "net_eur", "is_active")
    list_filter = ("is_active", "country_code", "method")
    search_fields = ("method__code", "method__name", "country_code")
    autocomplete_fields = ("method",)
