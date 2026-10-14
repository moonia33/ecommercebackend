from __future__ import annotations

from django.db import models

from .shared import _normalize_and_validate_translation_language_code


class ContentBlock(models.Model):
    class Placement(models.TextChoices):
        PRODUCT_DETAIL = "product_detail", "Product detail"
        CART = "cart", "Cart"
        CHECKOUT = "checkout", "Checkout"
        ORDER_CONFIRMATION = "order_confirmation", "Order confirmation"
        GLOBAL = "global", "Global"

    class BlockType(models.TextChoices):
        RICH_TEXT = "rich_text", "Rich text"
        TABLE = "table", "Table"
        IMAGE = "image", "Image"
        CALLOUT = "callout", "Callout"
        ACCORDION = "accordion", "Accordion"
        ATTACHMENTS = "attachments", "Attachments"

    key = models.SlugField(max_length=100, unique=True)
    type = models.CharField(
        max_length=32, choices=BlockType.choices, default=BlockType.RICH_TEXT
    )
    placement = models.CharField(
        max_length=32, choices=Placement.choices, default=Placement.PRODUCT_DETAIL
    )

    is_active = models.BooleanField(default=True)
    priority = models.IntegerField(default=0)
    valid_from = models.DateField(null=True, blank=True)
    valid_to = models.DateField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-priority", "key"]

    def __str__(self) -> str:
        return self.key


class ContentBlockTranslation(models.Model):
    content_block = models.ForeignKey(
        ContentBlock, on_delete=models.CASCADE, related_name="translations"
    )
    language_code = models.CharField(max_length=10)
    title = models.CharField(max_length=255, blank=True, default="")

    markdown = models.TextField(blank=True, default="")
    payload = models.JSONField(blank=True, default=dict)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["content_block", "language_code"],
                name="uniq_content_block_translation",
            )
        ]

    def __str__(self) -> str:
        return f"{self.content_block.key}:{self.language_code}"

    def clean(self):
        super().clean()
        self.language_code = _normalize_and_validate_translation_language_code(self.language_code)
        cb = getattr(self, "content_block", None)
        if not cb:
            return

        if cb.type == ContentBlock.BlockType.RICH_TEXT:
            self.payload = {"markdown": self.markdown or ""}

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)


class ContentRule(models.Model):
    content_block = models.ForeignKey(
        ContentBlock, on_delete=models.CASCADE, related_name="rules"
    )

    is_active = models.BooleanField(default=True)
    priority = models.IntegerField(default=0)
    is_exclusive = models.BooleanField(default=False)

    channel = models.CharField(max_length=16, blank=True, default="")

    brand = models.ForeignKey(
        "Brand",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="content_rules",
    )
    category = models.ForeignKey(
        "Category",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="content_rules",
    )
    include_descendants = models.BooleanField(default=True)
    product_group = models.ForeignKey(
        "ProductGroup",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="content_rules",
    )
    product = models.ForeignKey(
        "Product",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="content_rules",
    )

    valid_from = models.DateField(null=True, blank=True)
    valid_to = models.DateField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-priority", "id"]

    def __str__(self) -> str:
        return f"rule:{self.id} -> {self.content_block.key}"
