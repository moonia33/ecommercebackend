from __future__ import annotations

from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import F, Q


class ProductRelation(models.Model):
    """Directed product recommendation edge.

    Manual rows are merchandising decisions (highest priority).
    Auto rows are generated from purchase co-occurrence.
    """

    class Kind(models.TextChoices):
        CROSS_SELL = "cross_sell", "Cross-sell"
        UPSELL = "upsell", "Upsell"
        ACCESSORY = "accessory", "Accessory"
        SIMILAR = "similar", "Similar product"

    class Source(models.TextChoices):
        MANUAL = "manual", "Manual"
        AUTO = "auto", "Auto (co-purchase)"

    source_product = models.ForeignKey(
        "catalog.Product",
        on_delete=models.CASCADE,
        related_name="outgoing_relations",
    )
    target_product = models.ForeignKey(
        "catalog.Product",
        on_delete=models.CASCADE,
        related_name="incoming_relations",
    )
    kind = models.CharField(
        max_length=20, choices=Kind.choices, default=Kind.CROSS_SELL)
    source = models.CharField(
        max_length=10, choices=Source.choices, default=Source.MANUAL)
    weight = models.DecimalField(
        max_digits=12, decimal_places=4, default=1,
        help_text="Manual: display priority. Auto: co-purchase order count.",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-weight", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["source_product", "target_product", "kind"],
                name="uniq_product_relation",
            ),
            models.CheckConstraint(
                check=~Q(source_product=F("target_product")),
                name="product_relation_not_self",
            ),
        ]
        indexes = [
            models.Index(fields=["kind", "source", "-weight"]),
            models.Index(fields=["source_product", "kind"]),
        ]

    def clean(self):
        super().clean()
        if self.source_product_id and self.source_product_id == self.target_product_id:
            raise ValidationError("Product cannot be related to itself.")

    def __str__(self) -> str:
        return f"{self.source_product_id} -> {self.target_product_id} ({self.kind})"


class ProductCooccur(models.Model):
    """Materialized co-purchase pair (order-level, undirected).

    product_a_id < product_b_id is enforced by convention (CheckConstraint).
    Rebuilt periodically from paid OrderLine data.
    """

    product_a = models.ForeignKey(
        "catalog.Product",
        on_delete=models.CASCADE,
        related_name="cooccur_a",
    )
    product_b = models.ForeignKey(
        "catalog.Product",
        on_delete=models.CASCADE,
        related_name="cooccur_b",
    )
    order_count = models.PositiveIntegerField(default=0)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-order_count"]
        constraints = [
            models.UniqueConstraint(
                fields=["product_a", "product_b"],
                name="uniq_product_cooccur",
            ),
            models.CheckConstraint(
                check=Q(product_a__lt=F("product_b")),
                name="product_cooccur_ordered_pair",
            ),
        ]
        indexes = [
            models.Index(fields=["product_a", "-order_count"]),
            models.Index(fields=["product_b", "-order_count"]),
        ]

    def __str__(self) -> str:
        return f"{self.product_a_id} <> {self.product_b_id} ({self.order_count})"
