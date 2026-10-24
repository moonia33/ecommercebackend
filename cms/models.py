from __future__ import annotations

from django.core.exceptions import ValidationError
from django.db import models

from api.i18n import get_supported_language_codes, validate_supported_language_code


class CmsCategory(models.Model):
    """Groups pages for the help center / info hub."""

    slug = models.SlugField(max_length=200)
    shop = models.ForeignKey(
        "api.Shop", null=True, blank=True,
        on_delete=models.PROTECT, related_name="cms_categories")
    icon = models.CharField(
        max_length=60, blank=True, default="",
        help_text="Lucide icon name shown in the help center grid")
    sort_order = models.IntegerField(default=0)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["sort_order", "slug"]
        constraints = [
            models.UniqueConstraint(
                fields=["shop", "slug"], name="uniq_cmscategory_shop_slug"),
        ]

    def __str__(self):
        return self.slug


class CmsCategoryTranslation(models.Model):
    category = models.ForeignKey(
        CmsCategory, on_delete=models.CASCADE, related_name="translations")
    language_code = models.CharField(max_length=8)
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True, default="")

    class Meta:
        unique_together = ("category", "language_code")

    def __str__(self):
        return f"{self.category.slug} [{self.language_code}]"

    def clean(self):
        super().clean()
        self.language_code = validate_supported_language_code(self.language_code)


class CmsPage(models.Model):
    slug = models.SlugField(max_length=200)
    shop = models.ForeignKey(
        "api.Shop",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="cms_pages",
    )
    category = models.ForeignKey(
        CmsCategory, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="pages")
    sort_order = models.IntegerField(default=0)
    is_active = models.BooleanField(default=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["slug"]
        indexes = [models.Index(fields=["shop", "slug", "is_active"])]
        constraints = [
            models.UniqueConstraint(fields=["shop", "slug"], name="uniq_cmspage_shop_slug"),
        ]

    def __str__(self) -> str:
        return self.slug


class CmsPageTranslation(models.Model):
    cms_page = models.ForeignKey(
        CmsPage,
        on_delete=models.CASCADE,
        related_name="translations",
    )
    language_code = models.CharField(max_length=8)

    title = models.CharField(max_length=255, blank=True, default="")
    seo_title = models.CharField(max_length=255, blank=True, default="")
    seo_description = models.TextField(blank=True, default="")

    hero_image = models.ImageField(upload_to="cms/pages/", blank=True, null=True)
    hero_image_alt = models.CharField(max_length=255, blank=True, default="")

    body_markdown = models.TextField(blank=True, default="")

    class Meta:
        unique_together = ("cms_page", "language_code")
        indexes = [
            models.Index(fields=["cms_page", "language_code"]),
        ]

    def __str__(self) -> str:
        return f"{self.cms_page.slug} [{self.language_code}]"

    def clean(self):
        super().clean()
        try:
            self.language_code = validate_supported_language_code(self.language_code)
        except ValueError:
            code = (self.language_code or "").strip().lower()
            allowed = get_supported_language_codes()
            raise ValidationError(
                {"language_code": f"Neleistinas language_code '{code}'. Leidziami: {', '.join(sorted(allowed))}"}
            )


class StoreLocation(models.Model):
    """Physical store shown on /parduotuves — structured so maps and
    pickup logic can consume it (unlike a free-form CMS page)."""

    shop = models.ForeignKey(
        "api.Shop", null=True, blank=True,
        on_delete=models.PROTECT, related_name="store_locations")
    name = models.CharField(max_length=200)
    line1 = models.CharField(max_length=255)
    city = models.CharField(max_length=120)
    postal_code = models.CharField(max_length=20, blank=True, default="")
    country_code = models.CharField(max_length=2, default="LT")
    phone = models.CharField(max_length=40, blank=True, default="")
    email = models.EmailField(blank=True, default="")
    latitude = models.DecimalField(
        max_digits=9, decimal_places=6, null=True, blank=True)
    longitude = models.DecimalField(
        max_digits=9, decimal_places=6, null=True, blank=True)
    # {"mon": "9:00–18:00", ..., "sun": "closed"} — keyed by weekday.
    opening_hours = models.JSONField(default=dict, blank=True)
    # ["pickup_point", "service", "showroom"] — capability tags.
    services = models.JSONField(default=list, blank=True)
    description = models.TextField(blank=True, default="")
    is_pickup_point = models.BooleanField(
        default=False,
        help_text="Customers can pick up orders here (click & collect)")
    is_active = models.BooleanField(default=True)
    sort_order = models.IntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["sort_order", "city", "name"]

    def __str__(self):
        return f"{self.name} ({self.city})"
