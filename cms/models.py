from __future__ import annotations

from django.db import models


class CmsPage(models.Model):
    slug = models.SlugField(max_length=200, unique=True)
    is_active = models.BooleanField(default=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["slug"]

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
