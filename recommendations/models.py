from __future__ import annotations

from django.db import models


class RecommendationSet(models.Model):
    class Kind(models.TextChoices):
        COMPLEMENTS = "complements", "Complements"
        UPSELL = "upsell", "Upsell"
        SIMILAR = "similar", "Similar"

    kind = models.CharField(max_length=32, choices=Kind.choices, default=Kind.COMPLEMENTS)
    name = models.CharField(max_length=255, blank=True, default="")
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=["kind", "is_active"]),
            models.Index(fields=["created_at"]),
        ]

    def __str__(self) -> str:
        label = self.name.strip() or "(no name)"
        return f"{self.kind}:{label}"


class RecommendationSetItem(models.Model):
    set = models.ForeignKey(
        RecommendationSet,
        on_delete=models.CASCADE,
        related_name="items",
    )
    product = models.ForeignKey(
        "catalog.Product",
        on_delete=models.CASCADE,
        related_name="recommendation_set_items",
    )
    sort_order = models.IntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["set", "product"], name="uniq_reco_set_product"),
        ]
        indexes = [
            models.Index(fields=["set", "sort_order", "id"]),
            models.Index(fields=["product", "id"]),
        ]

    def __str__(self) -> str:
        return f"{self.set_id}:{self.product_id}"
