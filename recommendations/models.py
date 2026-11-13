from __future__ import annotations

from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import F, Q
from django.utils import timezone


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

    `order_count` — žalias porų kiekis (admin matomumui).
    `weight` — time-decay svertas (half-life iš RECOMMENDATION_HALF_LIFE_DAYS).
    `score` — cosine-lift normalizacija: weight / sqrt(orders_a * orders_b),
    kad bestseller'iai nedomintų visų rekomendacijų.
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
    weight = models.FloatField(default=0)
    score = models.FloatField(default=0)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-score", "-order_count"]
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
            models.Index(fields=["product_a", "-score"]),
            models.Index(fields=["product_b", "-score"]),
        ]

    def __str__(self) -> str:
        return f"{self.product_a_id} <> {self.product_b_id} ({self.order_count})"


class ProductMomentum(models.Model):
    """Trumpalaikis pardavimų pagreitis — sezoninių šuolių gaudymui.

    momentum = paskutinių REC_MOMENTUM_RECENT_DAYS pardavimų santykis su
    baseline (REC_MOMENTUM_BASELINE_DAYS langu) vidurkiu, su clamp ribomis.
    Staiga atšalus poledinės įrangos momentum iššoka per kelias dienas —
    rekomendacijos pasislenka be jokio admin'o įsikišimo.
    """

    product = models.OneToOneField(
        "catalog.Product",
        on_delete=models.CASCADE,
        related_name="momentum",
    )
    momentum = models.FloatField(default=1.0)
    sales_recent = models.PositiveIntegerField(default=0)
    baseline_daily = models.FloatField(default=0)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-momentum"]

    def __str__(self) -> str:
        return f"{self.product_id} momentum={self.momentum:.2f}"


class SeasonalBoost(models.Model):
    """Sezoninis rekomendacijų boost/demote kontekstas.

    Dvi formos:
      - SCHEDULED: kasmet kartojamas MM-DD langukas (neršto draudimai,
        tipiniai sezonai) — užpildoma kartą, kartojasi kasmet;
      - MANUAL: admin'o trigger'is („pasirodė stintos", „susidarė ledas")
        su `expires_at` TTL — nereikia nieko išjunginėti.

    Taikoma kaip score multiplikatorius Tier 2/3 kandidatams ir
    popular_product_ids — request metu, efektas iškart. boost < 1
    demote'ina (draudimai — žmonės perka ir sezonui iš anksto, todėl
    ne slepiam, o numušam žemyn).
    """

    class Mode(models.TextChoices):
        SCHEDULED = "scheduled", "Scheduled (annual MM-DD window)"
        MANUAL = "manual", "Manual trigger"

    name = models.CharField(max_length=128)
    activity = models.ForeignKey(
        "catalog.Activity",
        null=True, blank=True,
        on_delete=models.CASCADE,
        related_name="seasonal_boosts",
        help_text="Veikla (poledinė žūklė, stintos...). Sritis apima ir vaikus.",
    )
    category = models.ForeignKey(
        "catalog.Category",
        null=True, blank=True,
        on_delete=models.CASCADE,
        related_name="+",
        help_text="Alternatyva veiklos targeting'ui (su MPTT vaikais).",
    )
    boost = models.DecimalField(
        max_digits=5, decimal_places=2, default=1.5,
        help_text=">1 boost'ina (pvz. 2.0), <1 demote'ina (pvz. 0.4).",
    )
    mode = models.CharField(max_length=16, choices=Mode.choices, default=Mode.SCHEDULED)
    start_mmdd = models.CharField(
        max_length=5, blank=True,
        help_text="SCHEDULED: 'MM-DD', pvz. '12-15'.",
    )
    end_mmdd = models.CharField(
        max_length=5, blank=True,
        help_text="SCHEDULED: 'MM-DD'; jei < start — langas per metų ribą.",
    )
    is_active = models.BooleanField(default=True)
    expires_at = models.DateTimeField(
        null=True, blank=True,
        help_text="MANUAL: auto-išsijungia po šio laiko (TTL).",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["mode", "name"]
        indexes = [
            models.Index(fields=["is_active", "mode"]),
        ]

    def clean(self):
        super().clean()
        import re

        if not self.activity_id and not self.category_id:
            raise ValidationError(
                "Reikia activity arba category target'o — kitaip boost'as nieko nepataikys."
            )
        if self.mode == self.Mode.SCHEDULED:
            for f in ("start_mmdd", "end_mmdd"):
                v = getattr(self, f) or ""
                if not re.fullmatch(r"(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])", v):
                    raise ValidationError({f: "Reikalingas 'MM-DD' formatas."})

    def is_currently_active(self, *, now=None) -> bool:
        if not self.is_active:
            return False
        now = now or timezone.now()
        if self.expires_at and self.expires_at <= now:
            return False
        if self.mode == self.Mode.MANUAL:
            return True
        if not self.start_mmdd or not self.end_mmdd:
            return False
        today = now.strftime("%m-%d")
        if self.start_mmdd <= self.end_mmdd:
            return self.start_mmdd <= today <= self.end_mmdd
        return today >= self.start_mmdd or today <= self.end_mmdd

    def __str__(self) -> str:
        return f"{self.name} (×{self.boost})"
