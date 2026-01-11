from __future__ import annotations

from django.contrib import admin
from django.db import models

from catalog.widgets import ToastUIMarkdownWidget

from .models import CmsPage, CmsPageTranslation


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
