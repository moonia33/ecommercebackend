from __future__ import annotations

from django.db import models

from .variants import Variant


class Warehouse(models.Model):
    class Kind(models.TextChoices):
        LOCAL = "local", "Local"
        REMOTE = "remote", "Remote (supplier/imported)"

    code = models.SlugField(max_length=100, unique=True)
    name = models.CharField(max_length=255)

    # local = our stock; remote = supplier/imported stock arriving later
    kind = models.CharField(
        max_length=16, choices=Kind.choices, default=Kind.LOCAL)

    # ISO 3166-1 alpha-2 (e.g. LT)
    country_code = models.CharField(max_length=2)
    city = models.CharField(max_length=255, blank=True)

    # Simple dispatch lead time (days) for MVP; delivery ETA can be derived later
    dispatch_days_min = models.PositiveSmallIntegerField(default=0)
    dispatch_days_max = models.PositiveSmallIntegerField(default=0)

    is_active = models.BooleanField(default=True)
    sort_order = models.IntegerField(default=0)

    class Meta:
        ordering = ["sort_order", "code"]

    def __str__(self) -> str:
        return f"{self.code} - {self.name}"


class InventoryItem(models.Model):
    variant = models.ForeignKey(
        Variant, on_delete=models.CASCADE, related_name="inventory_items"
    )
    warehouse = models.ForeignKey(
        Warehouse, on_delete=models.PROTECT, related_name="inventory_items"
    )

    class ConditionGrade(models.TextChoices):
        NEW = "NEW", "New"
        RETURNED_A = "RETURNED_A", "Returned (A)"
        DAMAGED_B = "DAMAGED_B", "Damaged (B)"

    class OfferVisibility(models.TextChoices):
        NORMAL = "NORMAL", "Normal"
        OUTLET = "OUTLET", "Outlet"

    qty_on_hand = models.IntegerField(default=0)
    qty_reserved = models.IntegerField(default=0)
    cost_eur = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        null=True,
        blank=True,
        help_text="Optional per-warehouse purchase cost override.",
    )

    condition_grade = models.CharField(
        max_length=20,
        choices=ConditionGrade.choices,
        default=ConditionGrade.NEW,
    )
    offer_visibility = models.CharField(
        max_length=10,
        choices=OfferVisibility.choices,
        default=OfferVisibility.NORMAL,
    )
    offer_priority = models.IntegerField(default=0)
    offer_label = models.CharField(max_length=255, blank=True)
    offer_price_override_eur = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        null=True,
        blank=True,
    )
    offer_discount_percent = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
    )
    allow_additional_promotions = models.BooleanField(default=False)
    never_discount = models.BooleanField(default=False)

    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["variant", "warehouse"],
                name="uniq_inventory_item_variant_warehouse",
            ),
            models.CheckConstraint(
                check=models.Q(qty_on_hand__gte=0),
                name="chk_inventory_qty_on_hand_gte_0",
            ),
            models.CheckConstraint(
                check=models.Q(qty_reserved__gte=0),
                name="chk_inventory_qty_reserved_gte_0",
            ),
            models.CheckConstraint(
                check=models.Q(qty_reserved__lte=models.F("qty_on_hand")),
                name="chk_inventory_qty_reserved_lte_on_hand",
            ),
            models.CheckConstraint(
                check=models.Q(cost_eur__gte=0) | models.Q(
                    cost_eur__isnull=True),
                name="chk_inventory_cost_eur_gte_0",
            ),
            models.CheckConstraint(
                check=models.Q(offer_price_override_eur__gte=0)
                | models.Q(offer_price_override_eur__isnull=True),
                name="chk_inventory_offer_price_override_eur_gte_0",
            ),
            models.CheckConstraint(
                check=models.Q(offer_discount_percent__gte=0, offer_discount_percent__lte=100)
                | models.Q(offer_discount_percent__isnull=True),
                name="chk_inventory_offer_discount_percent_0_100",
            ),
        ]

    @property
    def qty_available(self) -> int:
        return max(0, int(self.qty_on_hand) - int(self.qty_reserved))

    def __str__(self) -> str:
        return f"{self.variant_id}@{self.warehouse.code}: {self.qty_available}"


class InventoryAdjustment(models.Model):
    """Audit'as kiekvienam rankiniam likučio pakeitimui (ops admin / API)."""

    class ReasonCode(models.TextChoices):
        CORRECTION = "correction", "Correction"
        COUNT = "count", "Stock count"
        DAMAGE = "damage", "Damage/write-off"
        RETURN = "return", "Return received"
        SUPPLIER_SYNC = "supplier_sync", "Supplier sync fix"
        OTHER = "other", "Other"

    inventory_item = models.ForeignKey(
        InventoryItem, on_delete=models.CASCADE, related_name="adjustments"
    )
    variant = models.ForeignKey(
        "catalog.Variant", on_delete=models.PROTECT, related_name="inventory_adjustments"
    )
    warehouse = models.ForeignKey(
        Warehouse, on_delete=models.PROTECT, related_name="inventory_adjustments"
    )
    delta = models.IntegerField()
    qty_before = models.IntegerField()
    qty_after = models.IntegerField()
    reason_code = models.CharField(
        max_length=32, choices=ReasonCode.choices, default=ReasonCode.OTHER
    )
    comment = models.CharField(max_length=500, blank=True)
    user = models.ForeignKey(
        "accounts.User", null=True, blank=True,
        on_delete=models.SET_NULL, related_name="inventory_adjustments",
    )
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at", "-id"]

    def __str__(self) -> str:
        return f"{self.variant_id}@{self.warehouse.code}: {self.delta:+d}"
