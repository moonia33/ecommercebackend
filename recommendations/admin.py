from __future__ import annotations

from django.contrib import admin

from .models import RecommendationSet, RecommendationSetItem


class RecommendationSetItemInline(admin.TabularInline):
    model = RecommendationSetItem
    extra = 0
    autocomplete_fields = ("product",)
    fields = ("product", "sort_order")
    ordering = ("sort_order", "id")


@admin.register(RecommendationSet)
class RecommendationSetAdmin(admin.ModelAdmin):
    list_display = ("id", "kind", "name", "is_active", "created_at")
    list_filter = ("kind", "is_active")
    search_fields = ("name",)
    ordering = ("-created_at",)
    inlines = (RecommendationSetItemInline,)
