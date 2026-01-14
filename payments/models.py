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


class NeopayConfig(models.Model):
    is_active = models.BooleanField(default=True)

    project_id = models.BigIntegerField()
    project_key = models.CharField(max_length=255)

    enable_bank_preselect = models.BooleanField(default=False)
    client_redirect_url = models.URLField(blank=True, default="")

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-id"]
        indexes = [
            models.Index(fields=["is_active", "-created_at"]),
        ]

    def __str__(self) -> str:
        return f"neopay:{self.project_id}"


class NeopayBank(models.Model):
    country_code = models.CharField(max_length=2)
    bic = models.CharField(max_length=32)
    name = models.CharField(max_length=200, blank=True, default="")
    logo_url = models.URLField(blank=True, default="")

    is_operating = models.BooleanField(default=True)
    is_enabled = models.BooleanField(default=True)
    sort_order = models.IntegerField(default=0)

    raw = models.JSONField(default=dict, blank=True)
    last_synced_at = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["country_code", "sort_order", "name", "bic"]
        constraints = [
            models.UniqueConstraint(fields=["country_code", "bic"], name="uniq_neopay_bank_country_bic"),
        ]
        indexes = [
            models.Index(fields=["country_code", "is_enabled", "sort_order"]),
            models.Index(fields=["bic"]),
        ]

    def __str__(self) -> str:
        return f"{self.country_code}:{self.bic}"
