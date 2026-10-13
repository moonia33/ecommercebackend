from __future__ import annotations

from django.contrib import admin

from .models import Shop, ShopConfig, ShopEmailConfig


class ShopConfigInline(admin.StackedInline):
    model = ShopConfig
    extra = 0
    max_num = 1


class ShopEmailConfigInline(admin.StackedInline):
    model = ShopEmailConfig
    extra = 0
    max_num = 1


@admin.register(Shop)
class ShopAdmin(admin.ModelAdmin):
    list_display = (
        "code",
        "name",
        "primary_domain",
        "default_language_code",
        "default_country_code",
        "currency",
        "is_active",
        "updated_at",
    )
    list_filter = ("is_active", "default_language_code", "default_country_code", "currency")
    search_fields = ("code", "name", "primary_domain", "additional_domains")
    ordering = ("code",)
    inlines = (ShopConfigInline, ShopEmailConfigInline)

    fieldsets = (
        ("Identity", {"fields": ("code", "name", "is_active")}),
        (
            "Domains",
            {"fields": ("primary_domain", "additional_domains")},
        ),
        (
            "Locale",
            {"fields": ("default_language_code", "default_country_code", "timezone", "currency")},
        ),
        ("Timestamps", {"fields": ("created_at", "updated_at")}),
    )
    readonly_fields = ("created_at", "updated_at")


@admin.register(ShopConfig)
class ShopConfigAdmin(admin.ModelAdmin):
    list_display = ("shop", "site_name", "enable_catalog", "enable_checkout", "updated_at")
    list_filter = ("enable_catalog", "enable_checkout")
    search_fields = ("shop__code", "shop__name", "site_name")
    autocomplete_fields = ("shop",)
    readonly_fields = ("created_at", "updated_at")


@admin.register(ShopEmailConfig)
class ShopEmailConfigAdmin(admin.ModelAdmin):
    list_display = ("shop", "is_active", "from_email", "smtp_host", "smtp_port", "updated_at")
    list_filter = ("is_active", "smtp_use_tls", "smtp_use_ssl")
    search_fields = ("shop__code", "shop__name", "from_email", "smtp_host")
    autocomplete_fields = ("shop",)
    readonly_fields = ("created_at", "updated_at")
