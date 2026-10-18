from __future__ import annotations

from django.db import models


class PaymentMethod(models.Model):
    class Kind(models.TextChoices):
        OFFLINE = "offline", "Offline"
        GATEWAY = "gateway", "Gateway"
        COD = "cod", "Cash on delivery"

    code = models.SlugField(max_length=50, unique=True)
    name = models.CharField(max_length=200)
    kind = models.CharField(max_length=20, choices=Kind.choices, default=Kind.OFFLINE)
    provider = models.CharField(max_length=50, blank=True, default="")

    is_active = models.BooleanField(default=True)
    sort_order = models.IntegerField(default=0)

    country_code = models.CharField(max_length=2, blank=True, default="")

    # When set, the method is offered only for these shipping methods
    # (blank = no restriction). E.g. COD only for LT courier/store pickup.
    allowed_shipping_methods = models.ManyToManyField(
        "shipping.ShippingMethod", blank=True,
        related_name="allowed_payment_methods")

    instructions = models.TextField(blank=True, default="")

    image = models.ImageField(upload_to="payment_methods/%Y/%m/", null=True, blank=True)

    bank_account_iban = models.CharField(max_length=64, blank=True, default="")
    bank_account_bic = models.CharField(max_length=32, blank=True, default="")
    bank_account_beneficiary = models.CharField(max_length=200, blank=True, default="")
    bank_account_bank_name = models.CharField(max_length=200, blank=True, default="")
    bank_account_purpose_template = models.CharField(max_length=255, blank=True, default="")

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["sort_order", "code"]
        indexes = [
            models.Index(fields=["is_active", "country_code", "sort_order"]),
            models.Index(fields=["code", "is_active"]),
        ]

    def __str__(self) -> str:
        return self.code

    def instructions_for_order(self, *, order_id: int | None = None) -> str:
        base = (self.instructions or "").strip()
        if base:
            return base

        parts: list[str] = []
        if (self.bank_account_beneficiary or "").strip():
            parts.append(f"Gavėjas: {self.bank_account_beneficiary.strip()}")
        if (self.bank_account_iban or "").strip():
            parts.append(f"IBAN: {self.bank_account_iban.strip()}")
        if (self.bank_account_bic or "").strip():
            parts.append(f"BIC/SWIFT: {self.bank_account_bic.strip()}")
        if (self.bank_account_bank_name or "").strip():
            parts.append(f"Bankas: {self.bank_account_bank_name.strip()}")

        purpose = (self.bank_account_purpose_template or "").strip()
        if purpose and order_id is not None:
            purpose = purpose.replace("{order_id}", str(order_id))
        if purpose:
            parts.append(f"Paskirtis: {purpose}")

        return "\n".join(parts).strip()


class ShopPaymentMethodPolicy(models.Model):
    shop = models.ForeignKey("api.Shop", on_delete=models.CASCADE, related_name="payment_policies")
    payment_method = models.ForeignKey(
        PaymentMethod,
        on_delete=models.CASCADE,
        related_name="shop_policies",
    )
    country_code = models.CharField(max_length=2, blank=True, default="")
    is_active = models.BooleanField(default=True)
    sort_order_override = models.IntegerField(null=True, blank=True)
    name_override = models.CharField(max_length=200, blank=True, default="")

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["shop", "payment_method", "country_code"],
                name="uniq_shop_payment_policy",
            )
        ]
        indexes = [
            models.Index(fields=["shop", "country_code", "is_active"]),
            models.Index(fields=["shop", "payment_method", "is_active"]),
        ]

    def __str__(self) -> str:
        cc = self.country_code or "*"
        return f"{self.shop.code}:{self.payment_method.code}:{cc}"
