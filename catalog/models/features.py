from __future__ import annotations

from django.db import models

from .product import Product
from .shared import _normalize_and_validate_translation_language_code


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


class FeatureTranslation(models.Model):
    feature = models.ForeignKey(
        Feature,
        on_delete=models.CASCADE,
        related_name="translations",
    )
    language_code = models.CharField(max_length=10)
    name = models.CharField(max_length=255, blank=True, default="")

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["feature", "language_code"],
                name="uniq_feature_translation",
            )
        ]

    def __str__(self) -> str:
        return f"{self.feature_id}:{self.language_code}"

    def clean(self):
        super().clean()
        self.language_code = _normalize_and_validate_translation_language_code(self.language_code)


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


class FeatureValueTranslation(models.Model):
    feature_value = models.ForeignKey(
        FeatureValue,
        on_delete=models.CASCADE,
        related_name="translations",
    )
    language_code = models.CharField(max_length=10)
    value = models.CharField(max_length=255, blank=True, default="")

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["feature_value", "language_code"],
                name="uniq_feature_value_translation",
            )
        ]

    def __str__(self) -> str:
        return f"{self.feature_value_id}:{self.language_code}"

    def clean(self):
        super().clean()
        self.language_code = _normalize_and_validate_translation_language_code(self.language_code)


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
    class DisplayType(models.TextChoices):
        SELECT = "select", "Select"
        RADIO = "radio", "Radio"
        SWATCH = "swatch", "Swatch"

    class SwatchType(models.TextChoices):
        HEX = "hex", "Hex"
        NAME = "name", "Name"
        IMAGE = "image", "Image"

    code = models.SlugField(max_length=100, unique=True)
    name = models.CharField(max_length=255)
    is_active = models.BooleanField(default=True)
    sort_order = models.IntegerField(default=0)
    display_type = models.CharField(
        max_length=20,
        choices=DisplayType.choices,
        default=DisplayType.RADIO,
    )
    swatch_type = models.CharField(
        max_length=20,
        choices=SwatchType.choices,
        null=True,
        blank=True,
        default=None,
    )

    class Meta:
        ordering = ["sort_order", "code"]

    def __str__(self) -> str:
        return self.code


class OptionTypeTranslation(models.Model):
    option_type = models.ForeignKey(
        OptionType,
        on_delete=models.CASCADE,
        related_name="translations",
    )
    language_code = models.CharField(max_length=10)
    name = models.CharField(max_length=255, blank=True, default="")

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["option_type", "language_code"],
                name="uniq_option_type_translation",
            )
        ]

    def __str__(self) -> str:
        return f"{self.option_type_id}:{self.language_code}"

    def clean(self):
        super().clean()
        self.language_code = _normalize_and_validate_translation_language_code(self.language_code)


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


class OptionValueTranslation(models.Model):
    option_value = models.ForeignKey(
        OptionValue,
        on_delete=models.CASCADE,
        related_name="translations",
    )
    language_code = models.CharField(max_length=10)
    label = models.CharField(max_length=255, blank=True, default="")

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["option_value", "language_code"],
                name="uniq_option_value_translation",
            )
        ]

    def __str__(self) -> str:
        return f"{self.option_value_id}:{self.language_code}"

    def clean(self):
        super().clean()
        self.language_code = _normalize_and_validate_translation_language_code(self.language_code)
