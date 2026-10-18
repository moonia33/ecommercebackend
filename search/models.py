from __future__ import annotations

from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone


def _terms_list(raw: str) -> list[str]:
    return [t.strip() for t in (raw or "").replace("\n", ",").split(",") if t.strip()]


class SearchSynonym(models.Model):
    """Synonym group pushed to Typesense (`syn-{id}`).

    Without `root`: multi-way synonym (any term expands to all terms).
    With `root`: one-way — queries for `root` also match the terms.
    """

    name = models.SlugField(max_length=100, unique=True)
    terms = models.TextField(
        help_text="Comma-separated terms, e.g. 'meškerė, meškere, spiningas'")
    root = models.CharField(
        max_length=255, blank=True, default="",
        help_text="Optional one-way synonym root term")
    is_active = models.BooleanField(default=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]

    def clean(self):
        super().clean()
        if len(_terms_list(self.terms)) < 2 and not self.root:
            raise ValidationError("Provide at least two terms, or a root term.")

    @property
    def terms_list(self) -> list[str]:
        return _terms_list(self.terms)

    @property
    def typesense_id(self) -> str:
        return f"syn-{self.id}"

    def __str__(self) -> str:
        return self.name


class SearchOverride(models.Model):
    """Typesense curation rule: pin/hide products for a query.

    `query="*"` applies to every search (global promotion).
    """

    class Match(models.TextChoices):
        EXACT = "exact", "Exact"
        CONTAINS = "contains", "Contains"

    name = models.SlugField(max_length=100, unique=True)
    query = models.CharField(
        max_length=255,
        help_text="Search query to match; '*' matches everything")
    match = models.CharField(
        max_length=10, choices=Match.choices, default=Match.EXACT)
    is_active = models.BooleanField(default=True)
    starts_at = models.DateTimeField(
        null=True, blank=True,
        help_text="Optional promotion start (for sales windows)")
    ends_at = models.DateTimeField(
        null=True, blank=True,
        help_text="Optional promotion end")
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]

    @property
    def typesense_id(self) -> str:
        return f"ovr-{self.id}"

    def is_currently_valid(self, *, now=None) -> bool:
        if not self.is_active:
            return False
        now = now or timezone.now()
        if self.starts_at and now < self.starts_at:
            return False
        if self.ends_at and now > self.ends_at:
            return False
        return True

    def __str__(self) -> str:
        return self.name


class SearchOverridePin(models.Model):
    override = models.ForeignKey(
        SearchOverride, on_delete=models.CASCADE, related_name="pins")
    product = models.ForeignKey(
        "catalog.Product", on_delete=models.CASCADE,
        related_name="search_pins")
    position = models.PositiveIntegerField(
        default=1, help_text="1-based position in results")

    class Meta:
        ordering = ["position", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["override", "product"], name="uniq_override_pin_product"),
        ]

    def __str__(self) -> str:
        return f"{self.override_id}:{self.position}={self.product_id}"


class SearchOverrideExclude(models.Model):
    override = models.ForeignKey(
        SearchOverride, on_delete=models.CASCADE, related_name="excludes")
    product = models.ForeignKey(
        "catalog.Product", on_delete=models.CASCADE,
        related_name="search_excludes")

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["override", "product"], name="uniq_override_exclude_product"),
        ]

    def __str__(self) -> str:
        return f"{self.override_id}:hide {self.product_id}"
