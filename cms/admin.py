from __future__ import annotations

from django import forms
from django.contrib import admin

from api.admin_i18n import apply_language_code_choices
from catalog.widgets import ToastUIMarkdownWidget

from .models import (
    CmsCategory, CmsCategoryTranslation, CmsPage, CmsPageTranslation,
    StoreLocation,
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

    class Form(forms.ModelForm):
        language_code = forms.ChoiceField(choices=())

        class Meta:
            model = CmsPageTranslation
            fields = "__all__"

        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            apply_language_code_choices(self, exclude_default=True)

    form = Form

    def formfield_for_dbfield(self, db_field, request, **kwargs):
        if db_field.name == "body_markdown":
            kwargs["widget"] = ToastUIMarkdownWidget
        return super().formfield_for_dbfield(db_field, request, **kwargs)


@admin.register(CmsPage)
class CmsPageAdmin(admin.ModelAdmin):
    list_display = ("slug", "shop", "category", "sort_order", "is_active", "updated_at")
    list_filter = ("shop", "category", "is_active")
    search_fields = ("slug", "shop__code", "shop__name")
    autocomplete_fields = ("shop",)
    list_editable = ("sort_order", "is_active")
    inlines = [CmsPageTranslationInline]


class CmsCategoryTranslationInline(admin.StackedInline):
    model = CmsCategoryTranslation
    extra = 0

    class Form(forms.ModelForm):
        language_code = forms.ChoiceField(choices=())

        class Meta:
            model = CmsCategoryTranslation
            fields = "__all__"

        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            apply_language_code_choices(self, exclude_default=True)

    form = Form


@admin.register(CmsCategory)
class CmsCategoryAdmin(admin.ModelAdmin):
    list_display = ("slug", "shop", "icon", "sort_order", "is_active")
    list_filter = ("shop", "is_active")
    search_fields = ("slug",)
    list_editable = ("sort_order", "is_active")
    inlines = [CmsCategoryTranslationInline]


@admin.register(StoreLocation)
class StoreLocationAdmin(admin.ModelAdmin):
    list_display = ("name", "city", "shop", "is_pickup_point", "sort_order", "is_active")
    list_filter = ("shop", "is_active", "is_pickup_point")
    search_fields = ("name", "city", "line1")
    list_editable = ("sort_order", "is_active")
