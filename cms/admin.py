from __future__ import annotations

from django.contrib import admin
from django.db import models

from catalog.widgets import ToastUIMarkdownWidget

from .models import (
    CmsPage,
    CmsPageTranslation,
    NavigationItem,
    NavigationItemTranslation,
    SiteNavigation,
)


class CmsPageTranslationInline(admin.StackedInline):
    model = CmsPageTranslation
    extra = 0
    show_change_link = True

    fieldsets = (
        (None, {"fields": ("language_code", "title")}),
        ("SEO", {"fields": ("seo_title", "seo_description"), "classes": ("collapse",)}),
        ("Paveikslėlis", {"fields": ("hero_image", "hero_image_alt"), "classes": ("collapse",)}),
        ("Turinys", {"fields": ("body_markdown",)}),
    )

    def formfield_for_dbfield(self, db_field, request, **kwargs):
        if db_field.name == "body_markdown":
            kwargs["widget"] = ToastUIMarkdownWidget
        return super().formfield_for_dbfield(db_field, request, **kwargs)


@admin.register(CmsPage)
class CmsPageAdmin(admin.ModelAdmin):
    list_display = ("slug", "is_active", "updated_at")
    list_filter = ("is_active",)
    search_fields = ("slug",)
    inlines = [CmsPageTranslationInline]


class NavigationItemTranslationInline(admin.TabularInline):
    model = NavigationItemTranslation
    extra = 0

    fields = ("language_code", "label", "badge", "badge_kind")


class NavigationItemInline(admin.TabularInline):
    model = NavigationItem
    extra = 0
    show_change_link = True
    autocomplete_fields = ("category", "brand", "cms_page", "parent")
    fields = (
        "parent",
        "sort_order",
        "is_active",
        "link_type",
        "url",
        "icon",
        "image",
        "image_url",
        "category",
        "brand",
        "cms_page",
        "open_in_new_tab",
    )


@admin.register(NavigationItem)
class NavigationItemAdmin(admin.ModelAdmin):
    list_display = ("id", "navigation", "parent", "link_type", "is_active", "sort_order", "updated_at")
    list_filter = ("navigation", "link_type", "is_active")
    search_fields = ("navigation__code", "url")
    autocomplete_fields = ("navigation", "parent", "category", "brand", "cms_page")
    inlines = [NavigationItemTranslationInline]


@admin.register(SiteNavigation)
class SiteNavigationAdmin(admin.ModelAdmin):
    list_display = ("site", "code", "is_active", "updated_at")
    list_filter = ("site", "is_active")
    search_fields = ("site__code", "code")
    autocomplete_fields = ("site",)
    inlines = [NavigationItemInline]
