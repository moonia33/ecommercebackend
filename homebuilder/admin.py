from __future__ import annotations

from django.contrib import admin
from django.db import models

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


class HomeSectionTranslationInline(admin.StackedInline):
    model = HomeSectionTranslation
    extra = 0


class HeroSlideTranslationInline(admin.StackedInline):
    model = HeroSlideTranslation
    extra = 0


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

    def formfield_for_dbfield(self, db_field, request, **kwargs):
        if db_field.name == "markdown":
            kwargs["widget"] = ToastUIMarkdownWidget
        return super().formfield_for_dbfield(db_field, request, **kwargs)


class NewsletterSectionTranslationInline(admin.StackedInline):
    model = NewsletterSectionTranslation
    extra = 0


@admin.register(HomePage)
class HomePageAdmin(admin.ModelAdmin):
    list_display = ("site", "code", "is_active", "updated_at")
    list_filter = ("site", "is_active")
    search_fields = ("code",)

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
