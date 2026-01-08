from __future__ import annotations

from django.contrib import admin

from .models import PaymentMethod


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
