from __future__ import annotations

from django.conf import settings
from django.db import models


class ShopBillingProfile(models.Model):
    """Per-shop invoice issuer identity + numbering config.

    Seller legal data is snapshotted onto each Invoice at issue time,
    so edits here never mutate already-issued documents.
    """

    shop = models.OneToOneField(
        "api.Shop", on_delete=models.CASCADE, related_name="billing_profile")

    legal_name = models.CharField(max_length=200, blank=True, default="")
    company_code = models.CharField(max_length=64, blank=True, default="")
    vat_code = models.CharField(max_length=64, blank=True, default="")
    address_line1 = models.CharField(max_length=200, blank=True, default="")
    city = models.CharField(max_length=120, blank=True, default="")
    postal_code = models.CharField(max_length=32, blank=True, default="")
    country_code = models.CharField(max_length=2, blank=True, default="LT")
    email = models.EmailField(blank=True, default="")
    phone = models.CharField(max_length=40, blank=True, default="")
    bank_name = models.CharField(max_length=120, blank=True, default="")
    iban = models.CharField(max_length=64, blank=True, default="")
    swift = models.CharField(max_length=16, blank=True, default="")

    # Numbering: SF-2026-0001 / KRF-2026-0001 style. Per-kind prefix.
    invoice_prefix = models.CharField(max_length=16, blank=True, default="SF")
    credit_prefix = models.CharField(max_length=16, blank=True, default="KRF")
    number_digits = models.PositiveSmallIntegerField(default=4)

    # PDF: Django template name; empty -> billing/invoice.html default.
    pdf_template = models.CharField(max_length=200, blank=True, default="")
    invoice_footer = models.TextField(blank=True, default="")

    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Shop billing profile"
        verbose_name_plural = "Shop billing profiles"

    def __str__(self) -> str:
        return f"Billing profile: {self.shop_id}"


class InvoiceSeries(models.Model):
    """Atomic per-shop/year/kind counter — the numbering source of truth."""

    class Kind(models.TextChoices):
        SALES = "sales", "Sales invoice"
        CREDIT = "credit", "Credit invoice"

    shop = models.ForeignKey(
        "api.Shop", on_delete=models.CASCADE, related_name="invoice_series")
    kind = models.CharField(max_length=16, choices=Kind.choices)
    year = models.PositiveIntegerField()
    next_number = models.PositiveIntegerField(default=1)

    class Meta:
        unique_together = ("shop", "kind", "year")
        verbose_name = "Invoice series"
        verbose_name_plural = "Invoice series"

    def __str__(self) -> str:
        return f"{self.shop_id} {self.kind} {self.year} → {self.next_number}"


def private_file_storage():
    """Invoices/labels never go to the public CDN bucket."""
    from django.core.files.storage import storages
    try:
        return storages["private"]
    except Exception:
        return storages["default"]


def invoice_pdf_upload_to(instance: "Invoice", filename: str) -> str:
    y = instance.issue_date.year if instance.issue_date else 0
    return f"invoices/{y}/{instance.number or 'invoice'}.pdf"


class Invoice(models.Model):
    """Immutable issued invoice snapshot. Never edited after issue."""

    class Kind(models.TextChoices):
        SALES = "sales", "Sales invoice"
        CREDIT = "credit", "Credit invoice"

    class Status(models.TextChoices):
        ISSUED = "issued", "Issued"
        CANCELLED = "cancelled", "Cancelled"

    number = models.CharField(max_length=40, unique=True)
    kind = models.CharField(max_length=16, choices=Kind.choices,
                            default=Kind.SALES)
    status = models.CharField(
        max_length=16, choices=Status.choices, default=Status.ISSUED)

    order = models.ForeignKey(
        "checkout.Order", on_delete=models.PROTECT, related_name="invoices")
    shop = models.ForeignKey(
        "api.Shop", null=True, blank=True,
        on_delete=models.PROTECT, related_name="invoices")
    credit_of = models.ForeignKey(
        "self", null=True, blank=True,
        on_delete=models.PROTECT, related_name="credit_notes")

    issue_date = models.DateField()
    issued_at = models.DateTimeField(auto_now_add=True)
    currency = models.CharField(max_length=3, default="EUR")
    language_code = models.CharField(max_length=8, blank=True, default="")

    # Immutable snapshots (JSON): legal requirement — do not recompute.
    seller = models.JSONField(default=dict, blank=True)
    buyer = models.JSONField(default=dict, blank=True)
    totals = models.JSONField(default=dict, blank=True)

    pdf = models.FileField(upload_to=invoice_pdf_upload_to,
                           storage=private_file_storage,
                           blank=True, default="")
    emailed_at = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-issue_date", "-id"]
        indexes = [
            models.Index(fields=["order", "kind"]),
            models.Index(fields=["issue_date"]),
        ]
        constraints = [
            # Exactly one active sales invoice per order.
            models.UniqueConstraint(
                fields=["order"],
                condition=models.Q(kind="sales") & ~models.Q(status="cancelled"),
                name="uniq_active_sales_invoice_per_order",
            ),
        ]

    def __str__(self) -> str:
        return self.number


class InvoiceLine(models.Model):
    class LineType(models.TextChoices):
        PRODUCT = "product", "Product"
        SHIPPING = "shipping", "Shipping"
        FEE = "fee", "Fee"
        DISCOUNT = "discount", "Discount"

    invoice = models.ForeignKey(
        Invoice, on_delete=models.CASCADE, related_name="lines")
    credited_line = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.SET_NULL,
        related_name="credit_rows",
        help_text="On credit invoices: the sales-invoice line being credited.",
    )
    order_line = models.ForeignKey(
        "checkout.OrderLine", null=True, blank=True,
        on_delete=models.SET_NULL, related_name="invoice_lines",
        help_text="Source order line (product lines) — used by returns.",
    )
    line_type = models.CharField(
        max_length=16, choices=LineType.choices, default=LineType.PRODUCT)
    name = models.CharField(max_length=255)
    sku = models.CharField(max_length=64, blank=True, default="")
    qty = models.PositiveIntegerField(default=1)

    unit_net = models.DecimalField(max_digits=12, decimal_places=2)
    vat_rate = models.DecimalField(max_digits=6, decimal_places=5,
                                   default="0")
    total_net = models.DecimalField(max_digits=12, decimal_places=2)
    total_vat = models.DecimalField(max_digits=12, decimal_places=2)
    total_gross = models.DecimalField(max_digits=12, decimal_places=2)

    sort_order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["sort_order", "id"]

    def __str__(self) -> str:
        return f"{self.name} x{self.qty}"
