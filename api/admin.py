from __future__ import annotations

from django import forms
from django.contrib import admin

from api.admin_i18n import apply_language_code_choices

from .models import Shop, ShopConfig, ShopEmailConfig, ShopLanguage


class ShopConfigInline(admin.StackedInline):
    model = ShopConfig
    extra = 0
    max_num = 1


class ShopEmailConfigInline(admin.StackedInline):
    model = ShopEmailConfig
    extra = 0
    max_num = 1


class ShopLanguageInline(admin.TabularInline):
    model = ShopLanguage
    extra = 0
    fields = ("language_code", "is_active", "is_default", "sort_order")
    ordering = ("-is_default", "sort_order", "language_code")

    class Form(forms.ModelForm):
        language_code = forms.ChoiceField(choices=())

        class Meta:
            model = ShopLanguage
            fields = "__all__"

        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            apply_language_code_choices(self, exclude_default=False)

    form = Form


@admin.register(Shop)
class ShopAdmin(admin.ModelAdmin):
    list_display = (
        "code",
        "name",
        "primary_domain",
        "resolved_default_language_code",
        "default_country_code",
        "currency",
        "is_active",
        "updated_at",
    )
    list_filter = ("is_active", "default_country_code", "currency")
    search_fields = ("code", "name", "primary_domain", "additional_domains")
    ordering = ("code",)
    inlines = (ShopLanguageInline, ShopConfigInline, ShopEmailConfigInline)

    fieldsets = (
        ("Identity", {"fields": ("code", "name", "is_active")}),
        (
            "Domains",
            {"fields": ("primary_domain", "additional_domains")},
        ),
        (
            "Locale",
            {"fields": ("resolved_default_language_code", "default_country_code", "timezone", "currency")},
        ),
        ("Timestamps", {"fields": ("created_at", "updated_at")}),
    )
    readonly_fields = ("resolved_default_language_code", "created_at", "updated_at")

    @admin.display(description="Default language code")
    def resolved_default_language_code(self, obj: Shop) -> str:
        row = (
            obj.languages.filter(is_active=True, is_default=True)
            .only("language_code")
            .first()
        )
        if row and (row.language_code or "").strip():
            return row.language_code
        return obj.default_language_code


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
