from django.contrib import admin

from .models import (
    SearchOverride,
    SearchOverrideExclude,
    SearchOverridePin,
    SearchSynonym,
)


@admin.register(SearchSynonym)
class SearchSynonymAdmin(admin.ModelAdmin):
    list_display = ("name", "root", "is_active", "updated_at")
    list_filter = ("is_active",)
    search_fields = ("name", "terms", "root")


class SearchOverridePinInline(admin.TabularInline):
    model = SearchOverridePin
    extra = 1
    autocomplete_fields = ("product",)


class SearchOverrideExcludeInline(admin.TabularInline):
    model = SearchOverrideExclude
    extra = 0
    autocomplete_fields = ("product",)


@admin.register(SearchOverride)
class SearchOverrideAdmin(admin.ModelAdmin):
    list_display = (
        "name", "query", "match", "is_active",
        "starts_at", "ends_at", "updated_at",
    )
    list_filter = ("is_active", "match")
    search_fields = ("name", "query")
    inlines = (SearchOverridePinInline, SearchOverrideExcludeInline)
