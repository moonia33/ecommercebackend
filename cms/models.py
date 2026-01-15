from __future__ import annotations

from django.db import models

from api.models import get_default_site_id


class CmsPage(models.Model):
    site = models.ForeignKey(
        "api.Site",
        default=get_default_site_id,
        on_delete=models.PROTECT,
        related_name="cms_pages",
    )
    slug = models.SlugField(max_length=200)
    is_active = models.BooleanField(default=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["slug"]
        constraints = [
            models.UniqueConstraint(fields=["site", "slug"], name="uniq_cms_page_site_slug"),
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


class SiteNavigation(models.Model):
    site = models.ForeignKey(
        "api.Site",
        default=get_default_site_id,
        on_delete=models.PROTECT,
        related_name="navigations",
    )
    code = models.SlugField(max_length=64)
    is_active = models.BooleanField(default=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["site__code", "code"]
        constraints = [
            models.UniqueConstraint(fields=["site", "code"], name="uniq_sitenav_site_code"),
        ]

    def __str__(self) -> str:
        return f"{self.site.code}:{self.code}"


class NavigationItem(models.Model):
    class LinkType(models.TextChoices):
        URL = "url", "URL"
        CATEGORY = "category", "Category"
        BRAND = "brand", "Brand"
        CMS_PAGE = "cms_page", "CMS page"

    navigation = models.ForeignKey(
        SiteNavigation,
        on_delete=models.CASCADE,
        related_name="items",
    )
    parent = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="children",
    )

    is_active = models.BooleanField(default=True)
    sort_order = models.IntegerField(default=0)

    link_type = models.CharField(max_length=16, choices=LinkType.choices, default=LinkType.URL)
    url = models.URLField(blank=True, default="")

    icon = models.CharField(max_length=64, blank=True, default="")
    image = models.ImageField(upload_to="cms/navigation/", blank=True, null=True)
    image_url = models.URLField(blank=True, default="")

    category = models.ForeignKey(
        "catalog.Category",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="navigation_items",
    )
    brand = models.ForeignKey(
        "catalog.Brand",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="navigation_items",
    )
    cms_page = models.ForeignKey(
        CmsPage,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="navigation_items",
    )

    open_in_new_tab = models.BooleanField(default=False)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["sort_order", "id"]
        indexes = [
            models.Index(fields=["navigation", "parent", "sort_order", "id"]),
            models.Index(fields=["navigation", "is_active"]),
        ]

    def __str__(self) -> str:
        return f"{self.navigation.code}:{self.id}"


class NavigationItemTranslation(models.Model):
    item = models.ForeignKey(
        NavigationItem,
        on_delete=models.CASCADE,
        related_name="translations",
    )
    language_code = models.CharField(max_length=8)
    label = models.CharField(max_length=255, blank=True, default="")
    badge = models.CharField(max_length=50, blank=True, default="")
    badge_kind = models.CharField(max_length=32, blank=True, default="")

    class Meta:
        unique_together = ("item", "language_code")
        indexes = [
            models.Index(fields=["item", "language_code"]),
        ]

    def __str__(self) -> str:
        return f"{self.item_id} [{self.language_code}]"
