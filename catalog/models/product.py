from __future__ import annotations

from django.core.exceptions import ValidationError
from django.db import models

from .shared import _normalize_and_validate_translation_language_code
from .tax import TaxClass
from .taxonomy import Brand, Category


class Product(models.Model):
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
        help_text="External product code within import_source.",
    )
    product_group_code = models.CharField(
        max_length=64,
        blank=True,
        default="",
        db_index=True,
        help_text="Supplier/import product-family code shared by the product and its variants.",
    )
    sku = models.CharField(max_length=64, unique=True)
    name = models.CharField(max_length=255)
    slug = models.SlugField(max_length=255, unique=True)
    # Markdown (rekomenduojama). Importuojant iš XML su HTML galima normalizuoti.
    description = models.TextField(blank=True)
    # Papildoma teisinė/saugos info (GPSR, ingredientai): {"gpsr": "...", "ingredients": "..."}
    additional_info = models.JSONField(blank=True, default=dict)

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

    # Atsarginė dalis (spare part): importas (is_part) permeta tokią prekę
    # į 'atsargines-dalys' kategoriją, kuri renderinama list template'u.
    is_part = models.BooleanField(default=False, db_index=True)

    # Returns: non-returnable products (e.g. perishables, custom orders)
    # are excluded from RMA flows. return_days=None -> shop/default window.
    returnable = models.BooleanField(default=True)
    return_days = models.PositiveSmallIntegerField(
        null=True, blank=True,
        help_text="Days after delivery the item can be returned "
                  "(empty -> DEFAULT_RETURN_DAYS).",
    )

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


class ProductVideo(models.Model):
    """Išorinė video nuoroda (pvz., YouTube) iš tiekėjo feed'o."""

    class Type(models.TextChoices):
        PRODUCT = "product", "Produkto video"
        INSTRUCTIONAL = "instructional", "Instruktažinis video"
        TUTORIAL = "tutorial", "Mokomasis video"

    product = models.ForeignKey(
        Product, on_delete=models.CASCADE, related_name="videos"
    )
    url = models.URLField(max_length=1000)
    video_type = models.CharField(
        max_length=20, choices=Type.choices, default=Type.PRODUCT
    )
    language_code = models.CharField(
        max_length=10,
        blank=True,
        default="",
        help_text="Video kalba (jei tiekėjas turi skirtingus video per kalbas).",
    )
    sort_order = models.IntegerField(default=0)

    class Meta:
        ordering = ["sort_order", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["product", "url"],
                name="uniq_product_video_url",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.product_id}:{self.video_type} {self.url}"


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
    # Retina (2x) square listing renditions — LISTING_IMAGE_SIZE_2X.
    listing_avif_2x = models.ImageField(
        upload_to="product-images/derived/%Y/%m/",
        null=True,
        blank=True,
    )
    listing_webp_2x = models.ImageField(
        upload_to="product-images/derived/%Y/%m/",
        null=True,
        blank=True,
    )
    # PDP main image: proportional, max edge DETAIL_IMAGE_SIZE (never upscaled).
    detail_avif = models.ImageField(
        upload_to="product-images/derived/%Y/%m/",
        null=True,
        blank=True,
    )
    detail_webp = models.ImageField(
        upload_to="product-images/derived/%Y/%m/",
        null=True,
        blank=True,
    )
    # Legacy/external image source (optional). Prefer `image`.
    # max_length=1000 – tiekėjų CDN URL'ai būna ilgesni nei default 200.
    image_url = models.URLField(blank=True, default="", max_length=1000)
    alt_text = models.CharField(max_length=255, blank=True)
    sort_order = models.IntegerField(default=0)
    # Variant-specific image: empty = shown for all variants (generic gallery),
    # non-empty = only when one of these variants is selected.
    variants = models.ManyToManyField(
        "Variant", blank=True, related_name="variant_images"
    )

    # External URL ingest state: downloaded into `image` (object storage) by
    # ingest_product_image_task. Empty = not needed (local upload or no url).
    INGEST_PENDING = "pending"
    INGEST_DONE = "done"
    INGEST_ERROR = "error"
    ingest_status = models.CharField(max_length=20, blank=True, default="")
    ingest_error = models.CharField(max_length=500, blank=True, default="")

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

    @property
    def listing_avif_2x_url(self) -> str:
        if self.listing_avif_2x:
            try:
                return self.listing_avif_2x.url
            except Exception:
                return ""
        return ""

    @property
    def listing_webp_2x_url(self) -> str:
        if self.listing_webp_2x:
            try:
                return self.listing_webp_2x.url
            except Exception:
                return ""
        return ""

    @property
    def detail_avif_url(self) -> str:
        if self.detail_avif:
            try:
                return self.detail_avif.url
            except Exception:
                return ""
        return ""

    @property
    def detail_webp_url(self) -> str:
        if self.detail_webp:
            try:
                return self.detail_webp.url
            except Exception:
                return ""
        return ""

    @property
    def original_url(self) -> str:
        if self.image:
            try:
                return self.image.url
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
