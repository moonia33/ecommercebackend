from __future__ import annotations

from django.contrib import admin

from .models import NeopayConfig, PaymentMethod


@admin.register(PaymentMethod)
class PaymentMethodAdmin(admin.ModelAdmin):
    list_display = (
        "code",
        "name",
        "kind",
        "provider",
        "is_active",
        "country_code",
        "sort_order",
        "updated_at",
    )
    list_filter = ("is_active", "kind", "provider", "country_code")
    search_fields = ("code", "name")
    ordering = ("sort_order", "code")


@admin.register(NeopayConfig)
class NeopayConfigAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "is_active",
        "project_id",
        "enable_bank_preselect",
        "widget_host",
        "client_redirect_url",
        "updated_at",
    )
    list_filter = ("is_active",)
    search_fields = ("project_id", "client_redirect_url")
