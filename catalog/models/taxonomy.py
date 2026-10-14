from __future__ import annotations

from django.db import models
from mptt.models import MPTTModel, TreeForeignKey

from .shared import _normalize_and_validate_translation_language_code


class Category(MPTTModel):
    external_presta_id = models.CharField(
        max_length=32,
        null=True,
        blank=True,
        unique=True,
        db_index=True,
        help_text="Original PrestaShop id_category used for import mapping.",
    )
    name = models.CharField(max_length=200)
    slug = models.SlugField(max_length=200, unique=True)
    parent = TreeForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="children",
    )
    description = models.TextField(
        blank=True, help_text="Markdown (rekomenduojama).")

    # Media: allow either upload or external URL (import/CDN)
    hero_image = models.ImageField(
        upload_to="category-hero/%Y/%m/",
        null=True,
        blank=True,
    )
    hero_image_url = models.URLField(blank=True, default="")
    hero_image_alt = models.CharField(max_length=255, blank=True)

    menu_icon = models.ImageField(
        upload_to="category-icons/%Y/%m/",
        null=True,
        blank=True,
    )
    menu_icon_url = models.URLField(blank=True, default="")
    menu_icon_alt = models.CharField(max_length=255, blank=True)

    # SEO
    seo_title = models.CharField(max_length=255, blank=True)
    seo_description = models.CharField(max_length=320, blank=True)
    seo_keywords = models.CharField(max_length=255, blank=True)
    is_active = models.BooleanField(default=True)

    class MPTTMeta:
        order_insertion_by = ["name"]

    class Meta:
        ordering = ["tree_id", "lft"]

    def __str__(self) -> str:
        return self.name

    @property
    def hero_url(self) -> str:
        if self.hero_image:
            try:
                return self.hero_image.url
            except Exception:
                return ""
        return self.hero_image_url

    @property
    def menu_icon_url_resolved(self) -> str:
        if self.menu_icon:
            try:
                return self.menu_icon.url
            except Exception:
                return ""
        return self.menu_icon_url


class CategoryTranslation(models.Model):
    category = models.ForeignKey(
        Category,
        on_delete=models.CASCADE,
        related_name="translations",
    )
    language_code = models.CharField(max_length=10)

    name = models.CharField(max_length=200, blank=True, default="")
    slug = models.SlugField(max_length=200)
    description = models.TextField(blank=True, default="")

    seo_title = models.CharField(max_length=255, blank=True, default="")
    seo_description = models.CharField(max_length=320, blank=True, default="")
    seo_keywords = models.CharField(max_length=255, blank=True, default="")

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["category", "language_code"],
                name="uniq_category_translation",
            ),
            models.UniqueConstraint(
                fields=["language_code", "slug"],
                name="uniq_category_translation_slug",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.category_id}:{self.language_code}"

    def clean(self):
        super().clean()
        self.language_code = _normalize_and_validate_translation_language_code(self.language_code)


class Brand(models.Model):
    external_presta_id = models.CharField(
        max_length=32,
        null=True,
        blank=True,
        unique=True,
        db_index=True,
        help_text="Original PrestaShop id_manufacturer used for import mapping.",
    )
    name = models.CharField(max_length=200)
    slug = models.SlugField(max_length=200, unique=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name


class BrandTranslation(models.Model):
    brand = models.ForeignKey(
        Brand,
        on_delete=models.CASCADE,
        related_name="translations",
    )
    language_code = models.CharField(max_length=10)

    name = models.CharField(max_length=200, blank=True, default="")
    slug = models.SlugField(max_length=200, blank=True, default="")

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["brand", "language_code"],
                name="uniq_brand_translation",
            ),
            models.UniqueConstraint(
                fields=["language_code", "slug"],
                name="uniq_brand_translation_slug",
                condition=~models.Q(slug=""),
            ),
        ]

    def __str__(self) -> str:
        return f"{self.brand_id}:{self.language_code}"

    def clean(self):
        super().clean()
        self.language_code = _normalize_and_validate_translation_language_code(self.language_code)


class ProductGroup(models.Model):
    code = models.SlugField(max_length=100, unique=True)
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name
