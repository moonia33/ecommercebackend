from __future__ import annotations

from django.db import models

from catalog.models import Brand, Category, Product, ProductGroup


class HomePage(models.Model):
    site = models.ForeignKey(
        "api.Site",
        on_delete=models.PROTECT,
        related_name="home_pages",
    )
    code = models.SlugField(max_length=64, default="home")
    is_active = models.BooleanField(default=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["code"]
        constraints = [
            models.UniqueConstraint(fields=["site", "code"], name="uniq_homepage_site_code"),
        ]

    def __str__(self) -> str:
        return self.code


class HomePageTranslation(models.Model):
    home_page = models.ForeignKey(HomePage, on_delete=models.CASCADE, related_name="translations")
    language_code = models.CharField(max_length=8)

    title = models.CharField(max_length=255, blank=True, default="")
    seo_title = models.CharField(max_length=255, blank=True, default="")
    seo_description = models.TextField(blank=True, default="")

    class Meta:
        unique_together = ("home_page", "language_code")
        indexes = [models.Index(fields=["home_page", "language_code"])]

    def __str__(self) -> str:
        return f"{self.home_page.code} [{self.language_code}]"


class HomeSection(models.Model):
    class Type(models.TextChoices):
        HERO = "hero", "Hero"
        PRODUCT_GRID = "product_grid", "Product grid"
        CATEGORY_GRID = "category_grid", "Category grid"
        RICH_TEXT = "rich_text", "Rich text"
        NEWSLETTER = "newsletter", "Newsletter"

    home_page = models.ForeignKey(HomePage, on_delete=models.CASCADE, related_name="sections")
    type = models.CharField(max_length=32, choices=Type.choices)
    sort_order = models.IntegerField(default=0)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["sort_order", "id"]

    def __str__(self) -> str:
        return f"{self.home_page.code}:{self.type}:{self.sort_order}"


class HomeSectionTranslation(models.Model):
    home_section = models.ForeignKey(HomeSection, on_delete=models.CASCADE, related_name="translations")
    language_code = models.CharField(max_length=8)

    title = models.CharField(max_length=255, blank=True, default="")

    class Meta:
        unique_together = ("home_section", "language_code")
        indexes = [models.Index(fields=["home_section", "language_code"])]

    def __str__(self) -> str:
        return f"{self.home_section_id} [{self.language_code}]"


class HeroSection(models.Model):
    home_section = models.OneToOneField(HomeSection, on_delete=models.CASCADE, related_name="hero")


class HeroSlide(models.Model):
    hero_section = models.ForeignKey(HeroSection, on_delete=models.CASCADE, related_name="slides")
    sort_order = models.IntegerField(default=0)

    image = models.ImageField(upload_to="home/hero/", blank=True, null=True)
    image_url = models.URLField(blank=True, default="")
    image_alt = models.CharField(max_length=255, blank=True, default="")

    cta_url = models.CharField(max_length=500, blank=True, default="")

    class Meta:
        ordering = ["sort_order", "id"]


class HeroSlideTranslation(models.Model):
    hero_slide = models.ForeignKey(HeroSlide, on_delete=models.CASCADE, related_name="translations")
    language_code = models.CharField(max_length=8)

    title = models.CharField(max_length=255, blank=True, default="")
    subtitle = models.CharField(max_length=255, blank=True, default="")
    cta_label = models.CharField(max_length=64, blank=True, default="")

    class Meta:
        unique_together = ("hero_slide", "language_code")
        indexes = [models.Index(fields=["hero_slide", "language_code"])]


class ProductGridSection(models.Model):
    class StockPolicy(models.TextChoices):
        IN_STOCK_FIRST = "in_stock_first", "In stock first"
        HIDE_OOS = "hide_oos", "Hide out of stock"

    home_section = models.OneToOneField(HomeSection, on_delete=models.CASCADE, related_name="product_grid")

    limit = models.IntegerField(default=12)
    stock_policy = models.CharField(max_length=32, choices=StockPolicy.choices, default=StockPolicy.IN_STOCK_FIRST)

    # Listing-like source filters
    category = models.ForeignKey(Category, null=True, blank=True, on_delete=models.SET_NULL)
    brand = models.ForeignKey(Brand, null=True, blank=True, on_delete=models.SET_NULL)
    product_group = models.ForeignKey(ProductGroup, null=True, blank=True, on_delete=models.SET_NULL)

    q = models.CharField(max_length=255, blank=True, default="")
    feature = models.CharField(max_length=500, blank=True, default="")
    option = models.CharField(max_length=500, blank=True, default="")

    sort = models.CharField(max_length=32, blank=True, default="")
    in_stock_only = models.BooleanField(default=False)


class ProductGridPinnedProduct(models.Model):
    product_grid = models.ForeignKey(ProductGridSection, on_delete=models.CASCADE, related_name="pinned")
    product = models.ForeignKey(Product, on_delete=models.CASCADE)
    sort_order = models.IntegerField(default=0)

    class Meta:
        ordering = ["sort_order", "id"]
        unique_together = ("product_grid", "product")


class CategoryGridSection(models.Model):
    home_section = models.OneToOneField(HomeSection, on_delete=models.CASCADE, related_name="category_grid")

    limit = models.IntegerField(default=12)

    # If root_category is set, use descendants (rules).
    root_category = models.ForeignKey(Category, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")


class CategoryGridPinnedCategory(models.Model):
    category_grid = models.ForeignKey(CategoryGridSection, on_delete=models.CASCADE, related_name="pinned")
    category = models.ForeignKey(Category, on_delete=models.CASCADE)
    sort_order = models.IntegerField(default=0)

    class Meta:
        ordering = ["sort_order", "id"]
        unique_together = ("category_grid", "category")


class RichTextSection(models.Model):
    home_section = models.OneToOneField(HomeSection, on_delete=models.CASCADE, related_name="rich_text")


class RichTextSectionTranslation(models.Model):
    rich_text_section = models.ForeignKey(RichTextSection, on_delete=models.CASCADE, related_name="translations")
    language_code = models.CharField(max_length=8)

    markdown = models.TextField(blank=True, default="")

    class Meta:
        unique_together = ("rich_text_section", "language_code")
        indexes = [models.Index(fields=["rich_text_section", "language_code"])]


class NewsletterSection(models.Model):
    home_section = models.OneToOneField(HomeSection, on_delete=models.CASCADE, related_name="newsletter")


class NewsletterSectionTranslation(models.Model):
    newsletter_section = models.ForeignKey(NewsletterSection, on_delete=models.CASCADE, related_name="translations")
    language_code = models.CharField(max_length=8)

    title = models.CharField(max_length=255, blank=True, default="")
    subtitle = models.CharField(max_length=255, blank=True, default="")
    cta_label = models.CharField(max_length=64, blank=True, default="")

    class Meta:
        unique_together = ("newsletter_section", "language_code")
        indexes = [models.Index(fields=["newsletter_section", "language_code"])]
