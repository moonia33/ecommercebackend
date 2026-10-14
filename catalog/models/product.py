from __future__ import annotations

from django.core.exceptions import ValidationError
from django.db import models

from .shared import _normalize_and_validate_translation_language_code
from .tax import TaxClass
from .taxonomy import Brand, Category


class Product(models.Model):
    external_presta_id = models.CharField(
        max_length=32,
        null=True,
        blank=True,
        unique=True,
        db_index=True,
        help_text="Original PrestaShop id_product used for import mapping.",
    )
    sku = models.CharField(max_length=64, unique=True)
    name = models.CharField(max_length=255)
    slug = models.SlugField(max_length=255, unique=True)
    # Markdown (rekomenduojama). Importuojant iš XML su HTML galima normalizuoti.
    description = models.TextField(blank=True)

    # SEO
    seo_title = models.CharField(max_length=255, blank=True)
    seo_description = models.CharField(max_length=320, blank=True)
    seo_keywords = models.CharField(max_length=255, blank=True)

    group = models.ForeignKey(
        "ProductGroup",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="products",
        help_text="Optional grouping for style/model (e.g. color as separate products)",
    )

    brand = models.ForeignKey(
        Brand, null=True, blank=True, on_delete=models.PROTECT, related_name="products"
    )
    category = models.ForeignKey(
        Category,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="products",
    )

    tax_class = models.ForeignKey(
        TaxClass,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="products",
        help_text="VAT class used to calculate gross/VAT for destination country.",
    )

    is_active = models.BooleanField(default=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]

    def __str__(self) -> str:
        return f"{self.sku} - {self.name}"


class ProductTranslation(models.Model):
    product = models.ForeignKey(
        Product,
        on_delete=models.CASCADE,
        related_name="translations",
    )
    language_code = models.CharField(max_length=10)

    name = models.CharField(max_length=255, blank=True, default="")
    slug = models.SlugField(max_length=255, blank=True, default="")
    description = models.TextField(blank=True, default="")
    seo_title = models.CharField(max_length=255, blank=True, default="")
    seo_description = models.CharField(max_length=320, blank=True, default="")
    seo_keywords = models.CharField(max_length=255, blank=True, default="")

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["product", "language_code"],
                name="uniq_product_translation",
            ),
            models.UniqueConstraint(
                fields=["language_code", "slug"],
                name="uniq_product_translation_slug",
                condition=~models.Q(slug=""),
            ),
        ]

    def __str__(self) -> str:
        return f"{self.product_id}:{self.language_code}"

    def clean(self):
        super().clean()
        self.language_code = _normalize_and_validate_translation_language_code(self.language_code)


class ProductImage(models.Model):
    product = models.ForeignKey(
        Product, on_delete=models.CASCADE, related_name="images")
    image = models.ImageField(
        upload_to="product-images/%Y/%m/",
        null=True,
        blank=True,
    )

    # Derived renditions for fast front-end usage (listing): medium AVIF + WEBP fallback.
    image_avif = models.ImageField(
        upload_to="product-images/derived/%Y/%m/",
        null=True,
        blank=True,
    )
    image_webp = models.ImageField(
        upload_to="product-images/derived/%Y/%m/",
        null=True,
        blank=True,
    )
    # Square (1:1) listing renditions: trim supplier whitespace, then fit+pad to a square.
    listing_avif = models.ImageField(
        upload_to="product-images/derived/%Y/%m/",
        null=True,
        blank=True,
    )
    listing_webp = models.ImageField(
        upload_to="product-images/derived/%Y/%m/",
        null=True,
        blank=True,
    )
    # Legacy/external image source (optional). Prefer `image`.
    image_url = models.URLField(blank=True, default="")
    alt_text = models.CharField(max_length=255, blank=True)
    sort_order = models.IntegerField(default=0)

    class Meta:
        ordering = ["sort_order", "id"]

    def __str__(self) -> str:
        return f"{self.product_id}:{self.sort_order}"

    def clean(self):
        super().clean()
        if not self.image and not self.image_url and not self.image_avif and not self.image_webp:
            raise ValidationError("Reikia pateikti arba failą, arba URL.")

    @property
    def url(self) -> str:
        # Prefer optimized renditions if available.
        if self.image_avif:
            try:
                return self.image_avif.url
            except Exception:
                return ""
        if self.image_webp:
            try:
                return self.image_webp.url
            except Exception:
                return ""

        if self.image:
            try:
                return self.image.url
            except Exception:
                return ""
        return self.image_url

    @property
    def avif_url(self) -> str:
        if self.image_avif:
            try:
                return self.image_avif.url
            except Exception:
                return ""
        return ""

    @property
    def webp_url(self) -> str:
        if self.image_webp:
            try:
                return self.image_webp.url
            except Exception:
                return ""
        return ""

    @property
    def listing_avif_url(self) -> str:
        if self.listing_avif:
            try:
                return self.listing_avif.url
            except Exception:
                return ""
        return ""

    @property
    def listing_webp_url(self) -> str:
        if self.listing_webp:
            try:
                return self.listing_webp.url
            except Exception:
                return ""
        return ""

    def save(self, *args, **kwargs):
        is_new_upload = (
            self.image
            and hasattr(self.image, "file")
            and not getattr(self, "_skip_image_processing", False)
        )
        super().save(*args, **kwargs)
        if is_new_upload:
            from django.db import transaction
            pk = self.pk

            def _dispatch(pk=pk):
                from catalog.tasks import process_product_image_task
                from config.celery import safe_delay
                safe_delay(process_product_image_task, pk)

            transaction.on_commit(_dispatch)
