"""Produktų badge'ai (marketingo žymės: „Naujiena“, „Akcija“, „Centrinis sandėlis“).

Badge kuriamas admine; prekei priskiriamas:
  - rankiniu būdu (ProductBadge.source=manual),
  - importu (source=import, kodai iš normalizer payload'o),
  - taisykle (BadgeRule → source=rule; taisyklė pati prideda ir nuima).
Atskirta nuo features: į parametrų lentelę nepatenka, Typesense'e – `badges`.
"""

from __future__ import annotations

from django.db import models
from django.db.models import Q
from django.utils import timezone


class Badge(models.Model):
    class Style(models.TextChoices):
        PRIMARY = "primary", "Primary"
        SUCCESS = "success", "Success"
        WARNING = "warning", "Warning"
        DANGER = "danger", "Danger"
        INFO = "info", "Info"
        DARK = "dark", "Dark"

    code = models.SlugField(max_length=64, unique=True)
    name = models.CharField(max_length=64, help_text="Rodomas tekstas (LT).")
    style = models.CharField(max_length=16, choices=Style.choices, default=Style.PRIMARY)
    color = models.CharField(
        max_length=7, blank=True, default="", help_text="Nebūtina #RRGGBB spalva (perrašo stilių)."
    )
    priority = models.IntegerField(default=0, help_text="Didesnis – rodomas pirmesnis.")
    is_active = models.BooleanField(default=True)
    valid_from = models.DateField(null=True, blank=True)
    valid_to = models.DateField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("-priority", "code")

    def __str__(self) -> str:
        return f"{self.name} ({self.code})"

    @staticmethod
    def live_q(prefix: str = "", today=None) -> Q:
        today = today or timezone.localdate()
        return (
            Q(**{f"{prefix}is_active": True})
            & (Q(**{f"{prefix}valid_from__isnull": True}) | Q(**{f"{prefix}valid_from__lte": today}))
            & (Q(**{f"{prefix}valid_to__isnull": True}) | Q(**{f"{prefix}valid_to__gte": today}))
        )


class BadgeTranslation(models.Model):
    badge = models.ForeignKey(Badge, on_delete=models.CASCADE, related_name="translations")
    language_code = models.CharField(max_length=10)
    name = models.CharField(max_length=64)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["badge", "language_code"], name="uniq_badge_translation_lang"
            )
        ]


class BadgeRule(models.Model):
    """Taisyklė: visos sąlygos AND; tuščia sąlyga netaikoma.

    Teksto sąlyga – tas pats matcher'is kaip EnrichmentRule. Feature sąlyga
    leidžia remtis enrichment'o priskirtomis reikšmėmis.
    """

    class MatcherType(models.TextChoices):
        CONTAINS = "contains", "Contains"
        REGEX = "regex", "Regex"

    name = models.CharField(max_length=128)
    badge = models.ForeignKey(Badge, on_delete=models.CASCADE, related_name="rules")
    is_active = models.BooleanField(default=True)

    brand = models.ForeignKey("Brand", null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    category = models.ForeignKey(
        "Category", null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    include_descendants = models.BooleanField(default=True)
    product_group = models.ForeignKey(
        "ProductGroup", null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    sku_prefix = models.CharField(
        max_length=32, blank=True, default="", help_text="Prekės arba varianto SKU pradžia, pvz. AL."
    )
    feature = models.ForeignKey(
        "Feature", null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    feature_value = models.ForeignKey(
        "FeatureValue",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="+",
        help_text="Tuščia – bet kuri feature reikšmė.",
    )
    matcher_type = models.CharField(
        max_length=16, choices=MatcherType.choices, default=MatcherType.CONTAINS
    )
    pattern = models.CharField(max_length=255, blank=True, default="")
    match_in_name = models.BooleanField(default=True)
    match_in_description = models.BooleanField(default=False)
    match_in_sku = models.BooleanField(default=False)
    in_stock_only = models.BooleanField(default=False)
    warehouse = models.ForeignKey(
        "Warehouse",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="+",
        help_text="Su likučiu (jei pažymėta) / nuolaida šiame sandėlyje.",
    )
    discounted_only = models.BooleanField(
        default=False, help_text="Tik prekės su sandėlio nuolaida ar mažesne sandėlio kaina."
    )
    created_within_days = models.PositiveIntegerField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("badge__code", "id")

    def __str__(self) -> str:
        return f"{self.name} → {self.badge.code}"


class ProductBadge(models.Model):
    class Source(models.TextChoices):
        MANUAL = "manual", "Manual"
        IMPORT = "import", "Import"
        RULE = "rule", "Rule"

    product = models.ForeignKey("Product", on_delete=models.CASCADE, related_name="badge_links")
    badge = models.ForeignKey(Badge, on_delete=models.CASCADE, related_name="product_links")
    source = models.CharField(max_length=8, choices=Source.choices, default=Source.MANUAL)
    rule = models.ForeignKey(
        BadgeRule, null=True, blank=True, on_delete=models.CASCADE, related_name="assignments"
    )
    valid_until = models.DateField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["product", "badge", "rule"],
                condition=Q(rule__isnull=False),
                name="uniq_product_badge_rule",
            ),
            models.UniqueConstraint(
                fields=["product", "badge", "source"],
                condition=Q(rule__isnull=True),
                name="uniq_product_badge_source",
            ),
        ]
        indexes = [models.Index(fields=["badge", "product"])]

    def __str__(self) -> str:
        return f"{self.product_id}:{self.badge_id} ({self.source})"
