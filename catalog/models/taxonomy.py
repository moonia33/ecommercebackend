from __future__ import annotations

from django.db import models
from mptt.models import MPTTModel, TreeForeignKey

from .shared import _normalize_and_validate_translation_language_code


class Category(MPTTModel):
    class ListTemplate(models.TextChoices):
        GRID = "grid", "Grid"
        LIST = "list", "List"

    import_source = models.CharField(
        max_length=64,
        blank=True,
        default="",
        help_text="Catalog import source identifier (e.g. 'normalizer').",
    )
    import_code = models.CharField(
        max_length=64,
        blank=True,
        default="",
        db_index=True,
        help_text="External category code within import_source.",
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
    # Kaip storefront renderina prekių sąrašą: 'grid' (kortelės, default)
    # arba 'list' (eilinė lentelė – pvz. atsarginių dalių kategorijai).
    list_template = models.CharField(
        max_length=8,
        choices=ListTemplate.choices,
        default=ListTemplate.GRID,
    )

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


class CategoryFacet(models.Model):
    """Per-category facet configuration (F4).

    When a category has enabled rows here, `build_facets_payload` returns
    only these facets, in `order`, with the configured `widget`. Categories
    without rows fall back to the default behaviour (all filterable
    features + used option types).
    """

    class Source(models.TextChoices):
        AUTO = "auto", "Auto"
        FEATURE = "feature", "Feature"
        OPTION = "option", "Option"

    class Widget(models.TextChoices):
        CHECKBOX = "checkbox", "Checkbox"
        RANGE = "range", "Range"

    category = models.ForeignKey(
        Category,
        on_delete=models.CASCADE,
        related_name="facet_config",
    )
    code = models.SlugField(
        max_length=100,
        help_text=(
            "Canonical kebab attribute code — the same code used in "
            "?feature=/?option=/?range= params. Matches Feature.code and "
            "OptionType.code (same-code facets merge)."
        ),
    )
    source = models.CharField(
        max_length=16,
        choices=Source.choices,
        default=Source.AUTO,
        help_text=(
            "Which side the code resolves on: 'auto' accepts either "
            "features or option types; 'feature'/'option' pin it to one."
        ),
    )
    widget = models.CharField(
        max_length=16,
        choices=Widget.choices,
        default=Widget.CHECKBOX,
        help_text="'checkbox' = value list; 'range' = numeric min/max slider.",
    )
    order = models.IntegerField(default=0)
    is_enabled = models.BooleanField(default=True)

    class Meta:
        ordering = ["order", "code"]
        constraints = [
            models.UniqueConstraint(
                fields=["category", "code"],
                name="uniq_category_facet",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.category_id}:{self.code}"


class Brand(models.Model):
    import_source = models.CharField(
        max_length=64,
        blank=True,
        default="",
        help_text="Catalog import source identifier (e.g. 'normalizer').",
    )
    import_code = models.CharField(
        max_length=64,
        blank=True,
        default="",
        db_index=True,
        help_text="External brand code within import_source.",
    )
    name = models.CharField(max_length=200)
    slug = models.SlugField(max_length=200, unique=True)
    description = models.TextField(
        blank=True, help_text="Markdown (rekomenduojama).")

    # Media: upload arba išorinis URL
    logo = models.ImageField(
        upload_to="brand-logos/%Y/%m/",
        null=True,
        blank=True,
    )
    logo_url = models.URLField(blank=True, default="")
    logo_alt = models.CharField(max_length=255, blank=True)
    # Generuojamas post_save signale (256px webp) — mažiems UI kontekstams.
    logo_thumb = models.ImageField(
        upload_to="brand-logos/thumbs/%Y/%m/",
        null=True,
        blank=True,
    )

    # SEO
    seo_title = models.CharField(max_length=255, blank=True)
    seo_description = models.CharField(max_length=320, blank=True)
    seo_keywords = models.CharField(max_length=255, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name

    @property
    def logo_url_resolved(self) -> str:
        if self.logo:
            try:
                return self.logo.url
            except Exception:
                return ""
        return self.logo_url

    @property
    def logo_thumb_url_resolved(self) -> str:
        if self.logo_thumb:
            try:
                return self.logo_thumb.url
            except Exception:
                pass
        return self.logo_url_resolved


class BrandTranslation(models.Model):
    brand = models.ForeignKey(
        Brand,
        on_delete=models.CASCADE,
        related_name="translations",
    )
    language_code = models.CharField(max_length=10)

    name = models.CharField(max_length=200, blank=True, default="")
    slug = models.SlugField(max_length=200, blank=True, default="")
    description = models.TextField(blank=True, default="")

    seo_title = models.CharField(max_length=255, blank=True, default="")
    seo_description = models.CharField(max_length=320, blank=True, default="")
    seo_keywords = models.CharField(max_length=255, blank=True, default="")

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
