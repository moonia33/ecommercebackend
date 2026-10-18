"""Catalog enrichment — rule-driven FeatureValue extraction.

EnrichmentRule turns unstructured product text (name/description/SKU) into
structured ProductFeatureValue assignments:

    product text -> matcher (contains/regex) -> extracted value
        -> value_format + value_template -> FeatureValue (created if new)
        -> ProductFeatureValue assigned to the product

Every run is audited: EnrichmentRun stores counters, EnrichmentMatch stores
one row per product×rule decision (assigned / skipped_exists /
skipped_conflict) including the matched field, matched text and the value
that was extracted — open a run in admin to see exactly what happened.

Runs are triggered from the EnrichmentRule admin actions
("Run selected rules (dry-run|apply)") or `./manage.py enrich_catalog`.
Always dry-run first: it writes the same audit rows without touching
ProductFeatureValue.
"""

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

    name = models.CharField(
        max_length=255,
        help_text="Free-form label so you remember what this rule does later.",
    )
    is_active = models.BooleanField(
        default=True,
        help_text="Inactive rules are skipped by every run.",
    )
    priority = models.IntegerField(
        default=0,
        help_text="Higher priority rules run first per product.",
    )

    feature = models.ForeignKey(
        "Feature",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="enrichment_rules",
        help_text=(
            "REQUIRED. The Feature whose FeatureValue is assigned when the rule "
            "matches (e.g. 'Lure Size'). Without this the rule is skipped."
        ),
    )

    matcher_type = models.CharField(
        max_length=20,
        choices=MatcherType.choices,
        default=MatcherType.CONTAINS,
        help_text=(
            "'Contains' = literal substring match on the normalized text "
            "(lowercase, collapsed whitespace). 'Regex' = Python regex search "
            "against the raw text, case-insensitive."
        ),
    )
    pattern = models.CharField(
        max_length=500,
        blank=True,
        default="",
        help_text=(
            "What to look for. Contains: plain text, e.g. 'Pro Shad'. "
            "Regex: e.g. '(\\d+(?:[.,]\\d+)?)\\s*cm\\b'. "
            "Empty pattern never matches."
        ),
    )
    extract_group = models.PositiveSmallIntegerField(
        default=0,
        help_text=(
            "Regex only. 0 = whole match; N = capture group N, e.g. pattern "
            "'(\\d+)\\s*cm' with group 1 extracts '14' from '14cm'."
        ),
    )

    value_template = models.CharField(
        max_length=255,
        blank=True,
        default="",
        help_text=(
            "Wraps the extracted value, e.g. '{{value}} cm' turns '14' into "
            "'14 cm'. Leave empty to store the extracted value as-is."
        ),
    )
    fixed_value = models.CharField(
        max_length=255,
        blank=True,
        default="",
        help_text=(
            "Contains matcher only: value stored when pattern matches, "
            "ignoring what was found (e.g. pattern 'Pro Shad' -> 'Soft lure')."
        ),
    )
    value_format = models.CharField(
        max_length=20,
        choices=ValueFormat.choices,
        default=ValueFormat.RAW,
        help_text=(
            "'Decimal trim' normalizes numbers: '10.00' -> '10', '2,5' -> "
            "'2.5'. 'Raw' keeps the extracted text untouched."
        ),
    )

    match_in_name = models.BooleanField(
        default=True,
        help_text="Search the product name. Most reliable field for specs.",
    )
    match_in_description = models.BooleanField(
        default=True,
        help_text=(
            "Search the product description. Beware: descriptions mention "
            "other numbers/specs and often cause false positives."
        ),
    )
    match_in_sku = models.BooleanField(
        default=False,
        help_text="Search the product SKU / supplier code.",
    )

    brand = models.ForeignKey(
        "Brand",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="enrichment_rules",
        help_text="Optional scope: only products of this brand.",
    )
    category = models.ForeignKey(
        "Category",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="enrichment_rules",
        help_text="Optional scope: only products in this category.",
    )
    include_descendants = models.BooleanField(
        default=True,
        help_text=(
            "With a category scope: also match products in all descendant "
            "categories. Uncheck to match the exact category only."
        ),
    )
    product_group = models.ForeignKey(
        "ProductGroup",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="enrichment_rules",
        help_text="Optional scope: only products of this product group.",
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
