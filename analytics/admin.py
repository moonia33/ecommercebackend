from __future__ import annotations

from django.contrib import admin

from .models import AnalyticsEvent, AnalyticsOutbox, FavoriteProduct, RecentlyViewedProduct, VisitorLink


@admin.register(AnalyticsEvent)
class AnalyticsEventAdmin(admin.ModelAdmin):
    list_display = ("site", "name", "occurred_at", "user", "visitor_id", "object_type", "object_id")
    list_filter = ("site", "name", "country_code", "channel")
    search_fields = ("visitor_id", "user__email", "object_type", "object_id")


@admin.register(VisitorLink)
class VisitorLinkAdmin(admin.ModelAdmin):
    list_display = ("site", "user", "visitor_id", "first_seen_at", "last_seen_at")
    search_fields = ("user__email", "visitor_id")


@admin.register(AnalyticsOutbox)
class AnalyticsOutboxAdmin(admin.ModelAdmin):
    list_display = ("provider", "status", "attempts", "created_at", "event")
    list_filter = ("provider", "status")
    search_fields = ("event__id",)


@admin.register(RecentlyViewedProduct)
class RecentlyViewedProductAdmin(admin.ModelAdmin):
    list_display = ("site", "user", "visitor_id", "product", "last_viewed_at")
    search_fields = ("visitor_id", "user__email", "product__slug", "product__sku")
    list_filter = ("site", "last_viewed_at",)


@admin.register(FavoriteProduct)
class FavoriteProductAdmin(admin.ModelAdmin):
    list_display = ("site", "user", "visitor_id", "product", "created_at")
    search_fields = ("visitor_id", "user__email", "product__slug", "product__sku")
    list_filter = ("site", "created_at")
    ordering = ("-created_at",)
