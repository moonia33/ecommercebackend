from __future__ import annotations

from django.conf import settings
from django.db import models


class EnrichmentRule(models.Model):
    class MatcherType(models.TextChoices):
        CONTAINS = "contains", "Contains"
        REGEX = "regex", "Regex"

    class MatchField(models.TextChoices):
        NAME = "name", "Name"
        DESCRIPTION = "description", "Description"
        SKU = "sku", "SKU"

    class ValueFormat(models.TextChoices):
        RAW = "raw", "Raw"
        DECIMAL_TRIM = "decimal_trim", "Decimal trim"

    name = models.CharField(max_length=255)
    is_active = models.BooleanField(default=True)
    priority = models.IntegerField(default=0)

    feature = models.ForeignKey(
        "Feature",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="enrichment_rules",
    )

    matcher_type = models.CharField(
        max_length=20,
        choices=MatcherType.choices,
        default=MatcherType.CONTAINS,
    )
    pattern = models.CharField(max_length=500, blank=True, default="")
    extract_group = models.PositiveSmallIntegerField(default=0)

    value_template = models.CharField(max_length=255, blank=True, default="")
    fixed_value = models.CharField(max_length=255, blank=True, default="")
    value_format = models.CharField(
        max_length=20,
        choices=ValueFormat.choices,
        default=ValueFormat.RAW,
    )

    match_in_name = models.BooleanField(default=True)
    match_in_description = models.BooleanField(default=True)
    match_in_sku = models.BooleanField(default=False)

    brand = models.ForeignKey(
        "Brand",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="enrichment_rules",
    )
    category = models.ForeignKey(
        "Category",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="enrichment_rules",
    )
    include_descendants = models.BooleanField(default=True)
    product_group = models.ForeignKey(
        "ProductGroup",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="enrichment_rules",
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-priority", "id"]

    def __str__(self) -> str:
        return f"rule:{self.id}"


class EnrichmentRun(models.Model):
    class Status(models.TextChoices):
        RUNNING = "running", "Running"
        DONE = "done", "Done"
        FAILED = "failed", "Failed"

    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.RUNNING
    )
    dry_run = models.BooleanField(default=True)
    triggered_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="enrichment_runs",
    )

    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    summary = models.JSONField(blank=True, default=dict)
    error = models.TextField(blank=True, default="")

    class Meta:
        ordering = ["-id"]

    def __str__(self) -> str:
        return f"run:{self.id}"


class EnrichmentMatch(models.Model):
    class Action(models.TextChoices):
        ASSIGNED = "assigned", "Assigned"
        SKIPPED_EXISTS = "skipped_exists", "Skipped (exists)"
        SKIPPED_CONFLICT = "skipped_conflict", "Skipped (conflict)"

    run = models.ForeignKey(
        EnrichmentRun, on_delete=models.CASCADE, related_name="matches"
    )
    rule = models.ForeignKey(
        EnrichmentRule,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="matches",
    )
    product = models.ForeignKey(
        "Product",
        on_delete=models.CASCADE,
        related_name="enrichment_matches",
    )

    matched_field = models.CharField(max_length=20, blank=True, default="")
    matched_text = models.CharField(max_length=500, blank=True, default="")
    extracted_value = models.CharField(max_length=255, blank=True, default="")
    action = models.CharField(max_length=30, choices=Action.choices)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-id"]

    def __str__(self) -> str:
        return f"match:{self.id}"
