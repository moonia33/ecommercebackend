from __future__ import annotations

from django.contrib import admin

from .models import (
    DeliveryRule,
    Holiday,
    ShippingCountry,
    ShippingCountryTranslation,
    ShippingMethod,
    ShippingRate,
)


class ShippingRateInline(admin.TabularInline):
    model = ShippingRate
    extra = 0


class ShippingCountryTranslationInline(admin.TabularInline):
    model = ShippingCountryTranslation
    extra = 0


@admin.register(ShippingMethod)
class ShippingMethodAdmin(admin.ModelAdmin):
    list_display = (
        "code",
        "name",
        "carrier_code",
        "image",
        "requires_pickup_point",
        "is_active",
        "sort_order",
    )
    list_filter = ("is_active", "carrier_code", "requires_pickup_point", "allowed_sites")
    search_fields = ("code", "name", "carrier_code")
    ordering = ("sort_order", "code")
    inlines = (ShippingRateInline,)
    filter_horizontal = ("allowed_sites",)


@admin.register(ShippingRate)
class ShippingRateAdmin(admin.ModelAdmin):
    list_display = ("method", "country_code", "net_eur", "is_active")
    list_filter = ("is_active", "country_code", "method")
    search_fields = ("method__code", "method__name", "country_code")
    autocomplete_fields = ("method",)


@admin.register(ShippingCountry)
class ShippingCountryAdmin(admin.ModelAdmin):
    list_display = ("code", "is_active", "sort_order")
    list_filter = ("is_active",)
    search_fields = ("code",)
    ordering = ("sort_order", "code")
    inlines = (ShippingCountryTranslationInline,)


@admin.register(Holiday)
class HolidayAdmin(admin.ModelAdmin):
    list_display = ("country_code", "date", "name", "is_active")
    list_filter = ("country_code", "is_active")
    search_fields = ("name",)
    ordering = ("country_code", "date")


@admin.register(DeliveryRule)
class DeliveryRuleAdmin(admin.ModelAdmin):
    list_display = ("site", "code", "kind", "priority", "channel", "warehouse", "is_active")
    list_filter = ("site", "is_active", "kind", "channel")
    search_fields = ("code", "name")
    autocomplete_fields = ("warehouse", "brand", "category", "product_group", "product")
    ordering = ("-priority", "code")
