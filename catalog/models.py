from __future__ import annotations

from io import BytesIO

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from django.db import models


class TaxClass(models.Model):
    code = models.SlugField(max_length=50, unique=True)
    name = models.CharField(max_length=255)
    is_active = models.BooleanField(default=True)
    sort_order = models.IntegerField(default=0)

    class Meta:
        ordering = ["sort_order", "code"]

    def __str__(self) -> str:
        return self.code


class TaxRate(models.Model):
    tax_class = models.ForeignKey(
        TaxClass, on_delete=models.CASCADE, related_name="rates"
    )

    # ISO 3166-1 alpha-2 (e.g. LT)
    country_code = models.CharField(max_length=2)

    # VAT rate as a fraction: 0.21 == 21%
    rate = models.DecimalField(max_digits=6, decimal_places=5)

    valid_from = models.DateField()
    valid_to = models.DateField(null=True, blank=True)

    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["country_code", "tax_class__code", "-valid_from"]
        constraints = [
            models.UniqueConstraint(
                fields=["tax_class", "country_code", "valid_from"],
                name="uniq_tax_rate_effective",
            ),
            models.CheckConstraint(
                check=models.Q(rate__gte=0) & models.Q(rate__lte=1),
                name="chk_tax_rate_between_0_and_1",
            ),
            models.CheckConstraint(
                check=models.Q(valid_to__gte=models.F("valid_from"))
                | models.Q(valid_to__isnull=True),
                name="chk_tax_rate_valid_to_gte_valid_from",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.country_code}:{self.tax_class.code}={self.rate}"


class Category(models.Model):
    name = models.CharField(max_length=200)
    slug = models.SlugField(max_length=200, unique=True)
    parent = models.ForeignKey(
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

    class Meta:
        ordering = ["name"]

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


class Brand(models.Model):
    name = models.CharField(max_length=200)
    slug = models.SlugField(max_length=200, unique=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name


class Product(models.Model):
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
        # Lightweight processing: resize large uploads + optimize.
        if self.image and hasattr(self.image, "file"):
            try:
                from PIL import Image
                import pillow_avif  # noqa: F401

                def _trim_whitespace(im: Image.Image, *, tol: int = 18):
                    """Trim near-solid background borders.

                    Heuristic: take corner pixel average as background and crop to pixels
                    that differ from it by more than `tol`.
                    """
                    if im.mode != "RGB":
                        im = im.convert("RGB")

                    w, h = im.size
                    if w < 10 or h < 10:
                        return im

                    px = im.load()
                    corners = [
                        px[0, 0],
                        px[w - 1, 0],
                        px[0, h - 1],
                        px[w - 1, h - 1],
                    ]
                    bg = (
                        sum(c[0] for c in corners) // 4,
                        sum(c[1] for c in corners) // 4,
                        sum(c[2] for c in corners) // 4,
                    )

                    def is_fg(rgb):
                        return (
                            abs(int(rgb[0]) - int(bg[0])) > tol
                            or abs(int(rgb[1]) - int(bg[1])) > tol
                            or abs(int(rgb[2]) - int(bg[2])) > tol
                        )

                    x0, y0 = w, h
                    x1, y1 = 0, 0
                    any_fg = False
                    step_x = 1 if w <= 800 else max(1, w // 800)
                    step_y = 1 if h <= 800 else max(1, h // 800)
                    for y in range(0, h, step_y):
                        for x in range(0, w, step_x):
                            if is_fg(px[x, y]):
                                any_fg = True
                                if x < x0:
                                    x0 = x
                                if y < y0:
                                    y0 = y
                                if x > x1:
                                    x1 = x
                                if y > y1:
                                    y1 = y

                    if not any_fg:
                        return im

                    # Expand bounds a little to avoid over-trimming.
                    pad = 2
                    x0 = max(0, x0 - pad)
                    y0 = max(0, y0 - pad)
                    x1 = min(w - 1, x1 + pad)
                    y1 = min(h - 1, y1 + pad)

                    if x1 <= x0 or y1 <= y0:
                        return im
                    return im.crop((x0, y0, x1 + 1, y1 + 1))

                def _fit_pad_square(im: Image.Image, *, edge: int, bg=(255, 255, 255)):
                    if im.mode != "RGB":
                        im = im.convert("RGB")
                    w, h = im.size
                    if w <= 0 or h <= 0:
                        return im

                    scale = min(edge / w, edge / h)
                    new_w = max(1, int(round(w * scale)))
                    new_h = max(1, int(round(h * scale)))
                    resized = im.resize((new_w, new_h), resample=Image.LANCZOS)

                    canvas = Image.new("RGB", (edge, edge), color=bg)
                    left = (edge - new_w) // 2
                    top = (edge - new_h) // 2
                    canvas.paste(resized, (left, top))
                    return canvas

                self.image.open()
                img = Image.open(self.image)

                # Normalize mode (AVIF/WEBP require RGB for most cases)
                if img.mode in ("P", "RGBA"):
                    img = img.convert("RGB")
                elif img.mode != "RGB":
                    img = img.convert("RGB")

                # Create medium renditions (used by listing). Keep originals as-is.
                medium_edge = int(getattr(settings, "MEDIUM_SIZE", 300) or 300)
                rendition = img.copy()
                if max(rendition.size) > medium_edge:
                    rendition.thumbnail((medium_edge, medium_edge))

                base_name = self.image.name.rsplit("/", 1)[-1]
                stem = base_name.rsplit(
                    ".", 1)[0] if "." in base_name else base_name

                avif_buf = BytesIO()
                rendition.save(avif_buf, format="AVIF", quality=60)
                avif_buf.seek(0)
                self.image_avif.save(f"{stem}_m{medium_edge}.avif", ContentFile(
                    avif_buf.read()), save=False)

                webp_buf = BytesIO()
                rendition.save(webp_buf, format="WEBP", quality=75, method=6)
                webp_buf.seek(0)
                self.image_webp.save(f"{stem}_m{medium_edge}.webp", ContentFile(
                    webp_buf.read()), save=False)

                # Listing square renditions: trim supplier whitespace then fit+pad to 1:1.
                listing_edge = int(
                    getattr(settings, "LISTING_IMAGE_SIZE",
                            medium_edge) or medium_edge
                )
                listing_tol = int(
                    getattr(settings, "LISTING_TRIM_TOLERANCE", 18) or 18)
                trimmed = _trim_whitespace(img.copy(), tol=listing_tol)
                square = _fit_pad_square(
                    trimmed, edge=listing_edge, bg=(255, 255, 255))

                listing_avif_buf = BytesIO()
                square.save(listing_avif_buf, format="AVIF", quality=60)
                listing_avif_buf.seek(0)
                self.listing_avif.save(
                    f"{stem}_sq{listing_edge}.avif",
                    ContentFile(listing_avif_buf.read()),
                    save=False,
                )

                listing_webp_buf = BytesIO()
                square.save(listing_webp_buf, format="WEBP",
                            quality=78, method=6)
                listing_webp_buf.seek(0)
                self.listing_webp.save(
                    f"{stem}_sq{listing_edge}.webp",
                    ContentFile(listing_webp_buf.read()),
                    save=False,
                )
            except Exception:
                # If processing fails, store original upload.
                pass

        return super().save(*args, **kwargs)


class ProductGroup(models.Model):
    code = models.SlugField(max_length=100, unique=True)
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name


class Feature(models.Model):
    code = models.SlugField(max_length=100, unique=True)
    name = models.CharField(max_length=255)
    is_filterable = models.BooleanField(default=True)
    allows_multiple = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)
    sort_order = models.IntegerField(default=0)

    class Meta:
        ordering = ["sort_order", "code"]

    def __str__(self) -> str:
        return self.code


class FeatureValue(models.Model):
    feature = models.ForeignKey(
        Feature, on_delete=models.CASCADE, related_name="values")
    value = models.CharField(max_length=255)
    sort_order = models.IntegerField(default=0)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["sort_order", "value"]
        constraints = [
            models.UniqueConstraint(
                fields=["feature", "value"],
                name="uniq_feature_value",
            )
        ]

    def __str__(self) -> str:
        return f"{self.feature.code}:{self.value}"


class ProductFeatureValue(models.Model):
    product = models.ForeignKey(
        Product, on_delete=models.CASCADE, related_name="feature_values")
    feature = models.ForeignKey(
        Feature, on_delete=models.PROTECT, related_name="product_values")
    feature_value = models.ForeignKey(
        FeatureValue, on_delete=models.PROTECT, related_name="product_values"
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["product", "feature_value"],
                name="uniq_product_feature_value",
            )
        ]

    def __str__(self) -> str:
        return f"{self.product_id}:{self.feature.code}={self.feature_value.value}"


class OptionType(models.Model):
    code = models.SlugField(max_length=100, unique=True)
    name = models.CharField(max_length=255)
    is_active = models.BooleanField(default=True)
    sort_order = models.IntegerField(default=0)

    class Meta:
        ordering = ["sort_order", "code"]

    def __str__(self) -> str:
        return self.code


class OptionValue(models.Model):
    option_type = models.ForeignKey(
        OptionType, on_delete=models.CASCADE, related_name="values")
    code = models.SlugField(max_length=100)
    label = models.CharField(max_length=255)
    sort_order = models.IntegerField(default=0)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["sort_order", "label"]
        constraints = [
            models.UniqueConstraint(
                fields=["option_type", "code"],
                name="uniq_option_value",
            )
        ]

    def __str__(self) -> str:
        return f"{self.option_type.code}:{self.label}"


class ProductOptionType(models.Model):
    product = models.ForeignKey(
        Product, on_delete=models.CASCADE, related_name="option_types")
    option_type = models.ForeignKey(
        OptionType, on_delete=models.PROTECT, related_name="products")
    sort_order = models.IntegerField(default=0)

    class Meta:
        ordering = ["sort_order", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["product", "option_type"],
                name="uniq_product_option_type",
            )
        ]

    def __str__(self) -> str:
        return f"{self.product_id}:{self.option_type.code}"


class Variant(models.Model):
    product = models.ForeignKey(
        Product, on_delete=models.CASCADE, related_name="variants")
    sku = models.CharField(max_length=64, unique=True)
    barcode = models.CharField(max_length=64, blank=True)
    name = models.CharField(max_length=255, blank=True)

    # MVP: EUR-only. Price is net (excl VAT).
    price_eur = models.DecimalField(max_digits=12, decimal_places=2)
    cost_eur = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        null=True,
        blank=True,
        help_text="Purchase cost (net) for margin/discount floor checks.",
    )
    is_active = models.BooleanField(default=True)

    # Shipping data (per-variant)
    weight_g = models.PositiveIntegerField(default=0)
    length_cm = models.PositiveIntegerField(default=0)
    width_cm = models.PositiveIntegerField(default=0)
    height_cm = models.PositiveIntegerField(default=0)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["sku"]

    def __str__(self) -> str:
        return self.sku


class VariantOptionValue(models.Model):
    variant = models.ForeignKey(
        Variant, on_delete=models.CASCADE, related_name="option_values")
    option_type = models.ForeignKey(OptionType, on_delete=models.PROTECT)
    option_value = models.ForeignKey(OptionValue, on_delete=models.PROTECT)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["variant", "option_type"],
                name="uniq_variant_option_type",
            )
        ]

    def __str__(self) -> str:
        return f"{self.variant_id}:{self.option_type.code}={self.option_value.label}"


class Warehouse(models.Model):
    code = models.SlugField(max_length=100, unique=True)
    name = models.CharField(max_length=255)

    # ISO 3166-1 alpha-2 (e.g. LT)
    country_code = models.CharField(max_length=2)
    city = models.CharField(max_length=255, blank=True)

    # Simple dispatch lead time (days) for MVP; delivery ETA can be derived later
    dispatch_days_min = models.PositiveSmallIntegerField(default=0)
    dispatch_days_max = models.PositiveSmallIntegerField(default=0)

    is_active = models.BooleanField(default=True)
    sort_order = models.IntegerField(default=0)

    class Meta:
        ordering = ["sort_order", "code"]

    def __str__(self) -> str:
        return f"{self.code} - {self.name}"


class InventoryItem(models.Model):
    variant = models.ForeignKey(
        Variant, on_delete=models.CASCADE, related_name="inventory_items"
    )
    warehouse = models.ForeignKey(
        Warehouse, on_delete=models.PROTECT, related_name="inventory_items"
    )

    qty_on_hand = models.IntegerField(default=0)
    qty_reserved = models.IntegerField(default=0)
    cost_eur = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        null=True,
        blank=True,
        help_text="Optional per-warehouse purchase cost override.",
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["variant", "warehouse"],
                name="uniq_inventory_item_variant_warehouse",
            ),
            models.CheckConstraint(
                check=models.Q(qty_on_hand__gte=0),
                name="chk_inventory_qty_on_hand_gte_0",
            ),
            models.CheckConstraint(
                check=models.Q(qty_reserved__gte=0),
                name="chk_inventory_qty_reserved_gte_0",
            ),
            models.CheckConstraint(
                check=models.Q(qty_reserved__lte=models.F("qty_on_hand")),
                name="chk_inventory_qty_reserved_lte_on_hand",
            ),
            models.CheckConstraint(
                check=models.Q(cost_eur__gte=0) | models.Q(
                    cost_eur__isnull=True),
                name="chk_inventory_cost_eur_gte_0",
            ),
        ]

    @property
    def qty_available(self) -> int:
        return max(0, int(self.qty_on_hand) - int(self.qty_reserved))

    def __str__(self) -> str:
        return f"{self.variant_id}@{self.warehouse.code}: {self.qty_available}"
