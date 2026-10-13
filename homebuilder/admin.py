from __future__ import annotations

from django import forms
from django.contrib import admin

from api.admin_i18n import apply_language_code_choices
from catalog.widgets import ToastUIMarkdownWidget

from .models import (
    CategoryGridPinnedCategory,
    CategoryGridSection,
    HeroSection,
    HeroSlide,
    HeroSlideTranslation,
    HomePage,
    HomePageTranslation,
    HomeSection,
    HomeSectionTranslation,
    NewsletterSection,
    NewsletterSectionTranslation,
    ProductGridPinnedProduct,
    ProductGridSection,
    RichTextSection,
    RichTextSectionTranslation,
)


class HomePageTranslationInline(admin.StackedInline):
    model = HomePageTranslation
    extra = 0

    class Form(forms.ModelForm):
        language_code = forms.ChoiceField(choices=())

        class Meta:
            model = HomePageTranslation
            fields = "__all__"

        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            apply_language_code_choices(self, exclude_default=True)

    form = Form


class HomeSectionTranslationInline(admin.StackedInline):
    model = HomeSectionTranslation
    extra = 0

    class Form(forms.ModelForm):
        language_code = forms.ChoiceField(choices=())

        class Meta:
            model = HomeSectionTranslation
            fields = "__all__"

        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            apply_language_code_choices(self, exclude_default=True)

    form = Form


class HeroSlideTranslationInline(admin.StackedInline):
    model = HeroSlideTranslation
    extra = 0

    class Form(forms.ModelForm):
        language_code = forms.ChoiceField(choices=())

        class Meta:
            model = HeroSlideTranslation
            fields = "__all__"

        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            apply_language_code_choices(self, exclude_default=True)

    form = Form


class HeroSlideInline(admin.StackedInline):
    model = HeroSlide
    extra = 0


class ProductGridPinnedProductInline(admin.TabularInline):
    model = ProductGridPinnedProduct
    extra = 0
    autocomplete_fields = ("product",)


class CategoryGridPinnedCategoryInline(admin.TabularInline):
    model = CategoryGridPinnedCategory
    extra = 0
    autocomplete_fields = ("category",)


class RichTextSectionTranslationInline(admin.StackedInline):
    model = RichTextSectionTranslation
    extra = 0

    class Form(forms.ModelForm):
        language_code = forms.ChoiceField(choices=())

        class Meta:
            model = RichTextSectionTranslation
            fields = "__all__"

        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            apply_language_code_choices(self, exclude_default=True)

    form = Form

    def formfield_for_dbfield(self, db_field, request, **kwargs):
        if db_field.name == "markdown":
            kwargs["widget"] = ToastUIMarkdownWidget
        return super().formfield_for_dbfield(db_field, request, **kwargs)


class NewsletterSectionTranslationInline(admin.StackedInline):
    model = NewsletterSectionTranslation
    extra = 0

    class Form(forms.ModelForm):
        language_code = forms.ChoiceField(choices=())

        class Meta:
            model = NewsletterSectionTranslation
            fields = "__all__"

        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            apply_language_code_choices(self, exclude_default=True)

    form = Form


@admin.register(HomePage)
class HomePageAdmin(admin.ModelAdmin):
    list_display = ("code", "shop", "is_active", "updated_at")
    list_filter = ("shop", "is_active",)
    search_fields = ("code", "shop__code", "shop__name")
    autocomplete_fields = ("shop",)

    inlines = [HomePageTranslationInline]


@admin.register(HomeSection)
class HomeSectionAdmin(admin.ModelAdmin):
    list_display = ("home_page", "type", "sort_order", "is_active")
    list_filter = ("type", "is_active")
    search_fields = ("home_page__code",)
    autocomplete_fields = ("home_page",)
    inlines = [HomeSectionTranslationInline]


@admin.register(HeroSection)
class HeroSectionAdmin(admin.ModelAdmin):
    autocomplete_fields = ("home_section",)
    inlines = [HeroSlideInline]


@admin.register(HeroSlide)
class HeroSlideAdmin(admin.ModelAdmin):
    list_display = ("hero_section", "sort_order")
    list_filter = ("hero_section",)
    inlines = [HeroSlideTranslationInline]


@admin.register(ProductGridSection)
class ProductGridSectionAdmin(admin.ModelAdmin):
    autocomplete_fields = ("home_section", "category", "brand", "product_group")
    list_display = ("home_section", "limit", "stock_policy", "sort", "in_stock_only")
    inlines = [ProductGridPinnedProductInline]


@admin.register(CategoryGridSection)
class CategoryGridSectionAdmin(admin.ModelAdmin):
    autocomplete_fields = ("home_section", "root_category")
    list_display = ("home_section", "limit", "root_category")
    inlines = [CategoryGridPinnedCategoryInline]


@admin.register(RichTextSection)
class RichTextSectionAdmin(admin.ModelAdmin):
    autocomplete_fields = ("home_section",)
    inlines = [RichTextSectionTranslationInline]


@admin.register(NewsletterSection)
class NewsletterSectionAdmin(admin.ModelAdmin):
    autocomplete_fields = ("home_section",)
    inlines = [NewsletterSectionTranslationInline]
