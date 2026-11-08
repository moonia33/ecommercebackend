from __future__ import annotations

from django.contrib import admin

from .models import (
    AnalyticsEvent,
    AnalyticsOutbox,
    CookieConsentRecord,
    RecentlyViewedProduct,
    VisitorLink,
)


@admin.register(AnalyticsEvent)
class AnalyticsEventAdmin(admin.ModelAdmin):
    list_display = ("name", "occurred_at", "user", "visitor_id", "object_type", "object_id")
    list_filter = ("name", "country_code", "channel")
    search_fields = ("visitor_id", "user__email", "object_type", "object_id")


@admin.register(VisitorLink)
class VisitorLinkAdmin(admin.ModelAdmin):
    list_display = ("user", "visitor_id", "first_seen_at", "last_seen_at")
    search_fields = ("user__email", "visitor_id")


@admin.register(AnalyticsOutbox)
class AnalyticsOutboxAdmin(admin.ModelAdmin):
    list_display = ("provider", "status", "attempts", "created_at", "event")
    list_filter = ("provider", "status")
    search_fields = ("event__id",)


@admin.register(RecentlyViewedProduct)
class RecentlyViewedProductAdmin(admin.ModelAdmin):
    list_display = ("user", "visitor_id", "product", "last_viewed_at")
    search_fields = ("visitor_id", "user__email", "product__slug", "product__sku")
    list_filter = ("last_viewed_at",)


@admin.register(CookieConsentRecord)
class CookieConsentRecordAdmin(admin.ModelAdmin):
    list_display = ("created_at", "consent_id", "shop", "version", "action",
                    "functional", "analytics", "marketing")
    list_filter = ("action", "version", "shop", "analytics", "marketing")
    search_fields = ("consent_id",)
    readonly_fields = [f.name for f in CookieConsentRecord._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
