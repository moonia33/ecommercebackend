from __future__ import annotations

from django.core.exceptions import ValidationError
from django.db import models
from mptt.models import MPTTModel, TreeForeignKey

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
    body_json = models.JSONField(
        null=True, blank=True,
        help_text="Tiptap JSON (ecommerceadmin redaktorius). Jei užpildyta, naudojama vietoj body_markdown.")

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


class Menu(models.Model):
    """Named navigation menu, resolved by the storefront by `code`.

    Standard practice (Shopify/WooCommerce nav): the site header,
    promo strip and future menus (footer columns, mobile drawer) are
    all Menu instances; items are an ordered MPTT tree so staff can
    drag them around in admin.
    """

    code = models.SlugField(max_length=40)
    shop = models.ForeignKey(
        "api.Shop", null=True, blank=True,
        on_delete=models.PROTECT, related_name="cms_menus")
    name = models.CharField(max_length=120)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["code"]
        constraints = [
            models.UniqueConstraint(
                fields=["shop", "code"], name="uniq_menu_shop_code"),
        ]

    def __str__(self):
        return f"{self.code} ({self.shop_id or 'global'})"


class MenuItem(MPTTModel):
    """One row in a navigation menu.

    `kind` decides which target field is authoritative; `link` uses the
    free-form URL, others resolve the storefront URL server-side so the
    FE never rebuilds routes. Translations override the label — with no
    translation the target object's own name is used.
    """

    class Kind(models.TextChoices):
        LINK = "link", "Link (free URL)"
        CATEGORY = "category", "Category"
        CMS_PAGE = "cms_page", "CMS page"
        BRAND = "brand", "Brand"
        PRESET = "preset", "Preset (storefront route)"

    class Preset(models.TextChoices):
        DISCOUNTED = "discounted", "Nuolaidos / Sale"
        NEW = "new", "Naujienos / New"
        BESTSELLERS = "bestsellers", "Populiariausios / Bestsellers"
        ALL_CATEGORIES = "all_categories", "Visos kategorijos"
        ALL_PRODUCTS = "all_products", "Visos prekės"
        GIFT_CARDS = "gift_cards", "Dovanų kortelės"
        HELP = "help", "Pagalba / Help center"
        STORES = "stores", "Parduotuvės / Stores"
        ACTIVITIES = "activities", "Pagal veiklą / Activities"
        FAVORITES = "favorites", "Mėgstamiausi / Favorites"
        ACCOUNT = "account", "Paskyra / Account"
        CART = "cart", "Krepšelis / Cart"

    class ShowIn(models.TextChoices):
        ALL = "all", "All"
        DESKTOP = "desktop", "Desktop only"
        MOBILE = "mobile", "Mobile only"

    menu = models.ForeignKey(
        Menu, on_delete=models.CASCADE, related_name="items")
    parent = TreeForeignKey(
        "self", null=True, blank=True,
        on_delete=models.CASCADE, related_name="children")
    kind = models.CharField(
        max_length=20, choices=Kind.choices, default=Kind.LINK)

    link_url = models.CharField(max_length=500, blank=True, default="")
    category = models.ForeignKey(
        "catalog.Category", null=True, blank=True,
        on_delete=models.SET_NULL, related_name="menu_items")
    cms_page = models.ForeignKey(
        CmsPage, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="menu_items")
    brand = models.ForeignKey(
        "catalog.Brand", null=True, blank=True,
        on_delete=models.SET_NULL, related_name="menu_items")
    preset = models.CharField(
        max_length=30, choices=Preset.choices, blank=True, default="")

    icon = models.CharField(
        max_length=60, blank=True, default="",
        help_text="Lucide icon name (e.g. 'percent', 'truck')")
    badge = models.CharField(
        max_length=30, blank=True, default="",
        help_text="Static badge text (overridden by translation)")
    highlight = models.BooleanField(
        default=False, help_text="Render with accent colour (e.g. Sale)")
    open_new_tab = models.BooleanField(default=False)
    show_in = models.CharField(
        max_length=10, choices=ShowIn.choices, default=ShowIn.ALL)
    is_active = models.BooleanField(default=True)

    class MPTTMeta:
        order_insertion_by = ["id"]

    class Meta:
        indexes = [models.Index(fields=["menu", "is_active"])]

    def __str__(self):
        return f"{self.menu.code}:{self.kind}:{self.preset or self.link_url or self.id}"


class MenuItemTranslation(models.Model):
    item = models.ForeignKey(
        MenuItem, on_delete=models.CASCADE, related_name="translations")
    language_code = models.CharField(max_length=8)
    title = models.CharField(max_length=255, blank=True)
    badge = models.CharField(max_length=30, blank=True, default="")

    class Meta:
        unique_together = ("item", "language_code")

    def __str__(self):
        return f"{self.item_id} [{self.language_code}]"

    def clean(self):
        super().clean()
        self.language_code = validate_supported_language_code(self.language_code)


class MegaMenuFeature(models.Model):
    """Promo card rendered inside the catalog mega-menu panel.

    `category=None` = default card shown on tabs without a specific
    feature. Scoped to a root category for targeted promos
    ("Žiemos pasiūlymas" under Žvejyba etc.).
    """

    shop = models.ForeignKey(
        "api.Shop", null=True, blank=True,
        on_delete=models.PROTECT, related_name="mega_menu_features")
    category = models.ForeignKey(
        "catalog.Category", null=True, blank=True,
        on_delete=models.CASCADE, related_name="mega_menu_features",
        help_text="Root category tab this card is shown on (empty = default)")
    image = models.ImageField(
        upload_to="cms/megamenu/%Y/%m/", null=True, blank=True)
    image_url = models.URLField(blank=True, default="")
    link_url = models.CharField(max_length=500, blank=True, default="")
    sort_order = models.IntegerField(default=0)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["sort_order", "id"]

    def __str__(self):
        return f"megamenu:{self.category_id or 'default'}:{self.id}"

    @property
    def image_resolved(self) -> str:
        if self.image:
            try:
                return self.image.url
            except Exception:
                return ""
        return self.image_url


class MegaMenuFeatureTranslation(models.Model):
    feature = models.ForeignKey(
        MegaMenuFeature, on_delete=models.CASCADE, related_name="translations")
    language_code = models.CharField(max_length=8)
    title = models.CharField(max_length=255, blank=True)
    text = models.TextField(blank=True, default="")
    cta_label = models.CharField(max_length=60, blank=True, default="")

    class Meta:
        unique_together = ("feature", "language_code")

    def __str__(self):
        return f"{self.feature_id} [{self.language_code}]"

    def clean(self):
        super().clean()
        self.language_code = validate_supported_language_code(self.language_code)


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


def _validate_lang(obj):
    try:
        obj.language_code = validate_supported_language_code(obj.language_code)
    except ValueError:
        code = (obj.language_code or "").strip().lower()
        allowed = get_supported_language_codes()
        raise ValidationError(
            {"language_code": f"Neleistinas language_code '{code}'. Leidziami: {', '.join(sorted(allowed))}"}
        )


class CmsMedia(models.Model):
    """Images uploaded from the rich text editor (pages and blog posts)."""

    image = models.ImageField(upload_to="cms/media/%Y/%m/")
    alt_text = models.CharField(max_length=255, blank=True, default="")
    width = models.PositiveIntegerField(null=True, blank=True)
    height = models.PositiveIntegerField(null=True, blank=True)
    uploaded_by = models.ForeignKey(
        "accounts.User", null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return self.image.name if self.image else f"media #{self.pk}"


class BlogCategory(models.Model):
    slug = models.SlugField(max_length=200)
    shop = models.ForeignKey(
        "api.Shop", null=True, blank=True, on_delete=models.PROTECT, related_name="blog_categories")
    sort_order = models.IntegerField(default=0)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["sort_order", "slug"]
        constraints = [
            models.UniqueConstraint(fields=["shop", "slug"], name="uniq_blog_category_shop_slug"),
        ]

    def __str__(self) -> str:
        return self.slug


class BlogCategoryTranslation(models.Model):
    category = models.ForeignKey(BlogCategory, on_delete=models.CASCADE, related_name="translations")
    language_code = models.CharField(max_length=8)
    name = models.CharField(max_length=200)
    description = models.TextField(blank=True, default="")

    class Meta:
        unique_together = ("category", "language_code")

    def __str__(self) -> str:
        return f"{self.name} [{self.language_code}]"

    def clean(self):
        super().clean()
        _validate_lang(self)


class BlogPost(models.Model):
    class Status(models.TextChoices):
        DRAFT = "draft", "Juodraštis"
        PUBLISHED = "published", "Publikuotas"

    shop = models.ForeignKey(
        "api.Shop", null=True, blank=True, on_delete=models.PROTECT, related_name="blog_posts",
        help_text="Tuščia = rodoma visose parduotuvėse")
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.DRAFT)
    published_at = models.DateTimeField(
        null=True, blank=True, help_text="Ateities data = suplanuotas publikavimas")
    author = models.ForeignKey(
        "accounts.User", null=True, blank=True, on_delete=models.SET_NULL, related_name="blog_posts")
    author_name = models.CharField(max_length=120, blank=True, default="")
    cover_image = models.ImageField(upload_to="cms/blog/", blank=True, null=True)
    categories = models.ManyToManyField(BlogCategory, blank=True, related_name="posts")
    related_products = models.ManyToManyField(
        "catalog.Product", blank=True, related_name="blog_posts")
    related_categories = models.ManyToManyField(
        "catalog.Category", blank=True, related_name="blog_posts")
    is_featured = models.BooleanField(default=False)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-published_at", "-id"]
        indexes = [models.Index(fields=["status", "published_at"])]

    def save(self, *args, **kwargs):
        if self.status == self.Status.PUBLISHED and self.published_at is None:
            from django.utils import timezone

            self.published_at = timezone.now()
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        return f"blog #{self.pk}"


class BlogPostTranslation(models.Model):
    post = models.ForeignKey(BlogPost, on_delete=models.CASCADE, related_name="translations")
    language_code = models.CharField(max_length=8)
    slug = models.SlugField(max_length=200)
    title = models.CharField(max_length=255)
    excerpt = models.TextField(blank=True, default="")
    body_json = models.JSONField(null=True, blank=True)
    seo_title = models.CharField(max_length=255, blank=True, default="")
    seo_description = models.TextField(blank=True, default="")
    cover_image_alt = models.CharField(max_length=255, blank=True, default="")
    reading_minutes = models.PositiveSmallIntegerField(default=0)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ("post", "language_code")
        indexes = [models.Index(fields=["language_code", "slug"])]

    def __str__(self) -> str:
        return f"{self.slug} [{self.language_code}]"

    def clean(self):
        super().clean()
        _validate_lang(self)

    def save(self, *args, **kwargs):
        from .richtext import doc_plain_text

        words = len(doc_plain_text(self.body_json).split()) if self.body_json else 0
        self.reading_minutes = max(1, round(words / 200)) if words else 0
        super().save(*args, **kwargs)


class BlogSlugRedirect(models.Model):
    """Old per-language slugs keep resolving (301) after a rename."""

    post = models.ForeignKey(BlogPost, on_delete=models.CASCADE, related_name="slug_redirects")
    language_code = models.CharField(max_length=8)
    old_slug = models.SlugField(max_length=200)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [models.Index(fields=["language_code", "old_slug"])]
