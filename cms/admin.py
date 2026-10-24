from __future__ import annotations

from django import forms
from django.contrib import admin
from mptt.admin import DraggableMPTTAdmin

from api.admin_i18n import apply_language_code_choices
from catalog.widgets import ToastUIMarkdownWidget

from .models import (
    CmsCategory, CmsCategoryTranslation, CmsPage, CmsPageTranslation,
    MegaMenuFeature, MegaMenuFeatureTranslation, Menu, MenuItem,
    MenuItemTranslation, StoreLocation,
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
    search_fields = ("slug", "shop__code", "shop__name",
                     "translations__title")
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


@admin.register(Menu)
class MenuAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "shop", "is_active", "item_count")
    list_filter = ("shop", "is_active")
    search_fields = ("code", "name")
    list_editable = ("is_active",)

    @admin.display(description="Item'ų")
    def item_count(self, obj):
        return obj.items.count()


class MenuItemTranslationInline(admin.StackedInline):
    model = MenuItemTranslation
    extra = 0

    class Form(forms.ModelForm):
        language_code = forms.ChoiceField(choices=())

        class Meta:
            model = MenuItemTranslation
            fields = "__all__"

        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            apply_language_code_choices(self, exclude_default=False)

    form = Form


@admin.register(MenuItem)
class MenuItemAdmin(DraggableMPTTAdmin):
    """Drag'n'drop medis — rikiavimas čia = storefront meniu rikiavimas."""
    list_display = (
        "tree_actions", "indented_title", "menu", "kind", "resolved_label",
        "icon", "badge", "highlight", "is_active",
    )
    list_display_links = ("indented_title",)
    list_filter = ("menu", "kind", "is_active", "highlight")
    search_fields = ("link_url", "preset", "translations__title")
    autocomplete_fields = ("category", "cms_page", "brand", "parent")
    inlines = [MenuItemTranslationInline]

    fieldsets = (
        (None, {"fields": ("menu", "parent", "kind", "is_active")}),
        ("Tikslas", {"fields": ("link_url", "category", "cms_page", "brand", "preset")}),
        ("Išvaizda", {"fields": ("icon", "badge", "highlight", "open_new_tab", "show_in")}),
    )

    @admin.display(description="Pavadinimas")
    def resolved_label(self, obj):
        t = obj.translations.filter(language_code="lt").first() or \
            obj.translations.first()
        if t and t.title:
            return t.title
        if obj.kind == MenuItem.Kind.PRESET:
            return obj.get_preset_display()
        if obj.kind == MenuItem.Kind.CATEGORY and obj.category_id:
            return str(obj.category)
        if obj.kind == MenuItem.Kind.BRAND and obj.brand_id:
            return str(obj.brand)
        if obj.kind == MenuItem.Kind.CMS_PAGE and obj.cms_page_id:
            return str(obj.cms_page)
        return obj.link_url or "—"


class MegaMenuFeatureTranslationInline(admin.StackedInline):
    model = MegaMenuFeatureTranslation
    extra = 0

    class Form(forms.ModelForm):
        language_code = forms.ChoiceField(choices=())

        class Meta:
            model = MegaMenuFeatureTranslation
            fields = "__all__"

        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            apply_language_code_choices(self, exclude_default=True)

    form = Form


@admin.register(MegaMenuFeature)
class MegaMenuFeatureAdmin(admin.ModelAdmin):
    list_display = (
        "id", "shop", "category", "sort_order", "has_image", "is_active")
    list_filter = ("shop", "is_active")
    search_fields = ("category__name", "link_url", "translations__title")
    list_editable = ("sort_order", "is_active")
    autocomplete_fields = ("category",)
    inlines = [MegaMenuFeatureTranslationInline]

    @admin.display(description="Paveikslas", boolean=True)
    def has_image(self, obj):
        return bool(obj.image or obj.image_url)


@admin.register(StoreLocation)
class StoreLocationAdmin(admin.ModelAdmin):
    list_display = ("name", "city", "shop", "is_pickup_point", "sort_order", "is_active")
    list_filter = ("shop", "is_active", "is_pickup_point")
    search_fields = ("name", "city", "line1")
    list_editable = ("sort_order", "is_active")
