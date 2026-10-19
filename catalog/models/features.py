from __future__ import annotations

from django.db import models

from .product import Product
from .shared import _normalize_and_validate_translation_language_code


class Feature(models.Model):
    code = models.SlugField(max_length=100, unique=True)
    name = models.CharField(max_length=255)
    is_filterable = models.BooleanField(default=True)
    allows_multiple = models.BooleanField(default=False)
    recommendation_boost = models.BooleanField(
        default=False,
        help_text=(
            "When enabled, products sharing values of this feature get a "
            "recommendation ranking boost (e.g. same fishing technique)."
        ),
    )
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


class FeatureValueGroup(models.Model):
    """Canonical filter value that aggregates many raw FeatureValues.

    Suppliers send near-duplicate feature values ("10-30", "10–35", "30 g").
    A group collects them so the storefront filter shows one checkbox
    ("iki 35 g") while the product page still displays the raw value.
    When min_value/max_value are set, `auto_assign` buckets values whose
    parsed numeric range fits inside the group's bounds (a rod rated
    '10-30' belongs to 'iki 35 g'; '20-100' does not).
    """

    feature = models.ForeignKey(
        Feature, on_delete=models.CASCADE, related_name="value_groups"
    )
    code = models.SlugField(
        max_length=100,
        help_text=(
            "Used in the ?feature=<feature_code>:<code> URL filter, e.g. "
            "'iki-35'. Unique within the feature."
        ),
    )
    label = models.CharField(
        max_length=255,
        help_text="Checkbox label shown in the filter, e.g. 'iki 35 g'.",
    )
    values = models.ManyToManyField(
        "FeatureValue",
        blank=True,
        related_name="groups",
        help_text="Raw feature values aggregated by this group.",
    )
    min_value = models.DecimalField(
        max_digits=12,
        decimal_places=3,
        null=True,
        blank=True,
        help_text=(
            "Optional lower bound for auto-assign. Empty = unbounded. "
            "'iki 35 g' = max 35, min empty."
        ),
    )
    max_value = models.DecimalField(
        max_digits=12,
        decimal_places=3,
        null=True,
        blank=True,
        help_text="Optional upper bound for auto-assign. Empty = unbounded.",
    )
    sort_order = models.IntegerField(default=0)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["sort_order", "code"]
        constraints = [
            models.UniqueConstraint(
                fields=["feature", "code"],
                name="uniq_feature_value_group",
            )
        ]

    def __str__(self) -> str:
        return f"{self.feature.code}:{self.label}"


class FeatureValue(models.Model):
    feature = models.ForeignKey(
        Feature, on_delete=models.CASCADE, related_name="values")
    value = models.CharField(max_length=255)
    label = models.CharField(
        max_length=255,
        blank=True,
        default="",
        help_text=(
            "Human-readable base-language label shown when no translation "
            "row wins. 'value' stays a normalized machine slug (facets, "
            "dedup); 'label' is what customers see."
        ),
    )
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


class VariantFeatureValue(models.Model):
    """Feature value belonging to one variant (weight/size differ per variant).

    Product-level `ProductFeatureValue` holds values shared by all variants;
    this model holds values that differ between variants. Display merges
    them — the variant's value wins for the same feature code.
    """

    variant = models.ForeignKey(
        "catalog.Variant", on_delete=models.CASCADE, related_name="feature_values")
    feature = models.ForeignKey(
        Feature, on_delete=models.PROTECT, related_name="variant_values")
    feature_value = models.ForeignKey(
        FeatureValue, on_delete=models.PROTECT, related_name="variant_values"
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["variant", "feature_value"],
                name="uniq_variant_feature_value",
            )
        ]

    def __str__(self) -> str:
        return f"{self.variant_id}:{self.feature.code}={self.feature_value.value}"


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


class OptionValueGroup(models.Model):
    """Canonical filter value that aggregates raw OptionValues.

    Same idea as FeatureValueGroup, but for variant option values
    (e.g. supplier size variants merged into one 'XXL' checkbox).
    """

    option_type = models.ForeignKey(
        OptionType, on_delete=models.CASCADE, related_name="value_groups"
    )
    code = models.SlugField(
        max_length=100,
        help_text=(
            "Used in the ?option=<type_code>:<code> URL filter. "
            "Unique within the option type."
        ),
    )
    label = models.CharField(
        max_length=255,
        help_text="Checkbox label shown in the filter.",
    )
    values = models.ManyToManyField(
        "OptionValue",
        blank=True,
        related_name="groups",
        help_text="Raw option values aggregated by this group.",
    )
    min_value = models.DecimalField(
        max_digits=12,
        decimal_places=3,
        null=True,
        blank=True,
        help_text="Optional lower bound for auto-assign. Empty = unbounded.",
    )
    max_value = models.DecimalField(
        max_digits=12,
        decimal_places=3,
        null=True,
        blank=True,
        help_text="Optional upper bound for auto-assign. Empty = unbounded.",
    )
    sort_order = models.IntegerField(default=0)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["sort_order", "code"]
        constraints = [
            models.UniqueConstraint(
                fields=["option_type", "code"],
                name="uniq_option_value_group",
            )
        ]

    def __str__(self) -> str:
        return f"{self.option_type.code}:{self.label}"


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
