from __future__ import annotations

from django.contrib import admin

from .models import SearchSynonym


@admin.register(SearchSynonym)
class SearchSynonymAdmin(admin.ModelAdmin):
    list_display = ("language_code", "term", "is_active")
    list_filter = ("language_code", "is_active")
    search_fields = ("term",)
    ordering = ("language_code", "term")
