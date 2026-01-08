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

    force_bank_bic = models.CharField(max_length=32, blank=True, default="")
    force_bank_name = models.CharField(max_length=200, blank=True, default="")

    banks_api_base_url = models.URLField(
        blank=True, default="https://psd2.neopay.lt/api"
    )

    widget_host = models.URLField(
        blank=True, default="https://psd2.neopay.lt/widget.html?"
    )
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
