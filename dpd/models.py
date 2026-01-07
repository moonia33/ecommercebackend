from __future__ import annotations

from django.db import models


class DpdLocker(models.Model):
    # Use DPD provided identifier (id/lockerId/code depending on API)
    locker_id = models.CharField(max_length=80, unique=True)

    country_code = models.CharField(max_length=2, blank=True, default="")
    city = models.CharField(max_length=120, blank=True, default="")
    name = models.CharField(max_length=255, blank=True, default="")
    street = models.CharField(max_length=255, blank=True, default="")
    postal_code = models.CharField(max_length=32, blank=True, default="")

    latitude = models.DecimalField(
        max_digits=9, decimal_places=6, null=True, blank=True)
    longitude = models.DecimalField(
        max_digits=9, decimal_places=6, null=True, blank=True)

    raw = models.JSONField(default=dict, blank=True)

    is_active = models.BooleanField(default=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["country_code", "city", "name", "locker_id"]
        indexes = [
            models.Index(fields=["country_code", "city"]),
            models.Index(fields=["locker_id"]),
        ]

    def __str__(self) -> str:
        parts = [p for p in [self.city, self.name, self.street] if p]
        label = " - ".join(parts) if parts else self.locker_id
        return f"{label} ({self.locker_id})"


class DpdConfig(models.Model):
    """DPD konfigūracija laikoma DB ir valdoma per admin.

    Paprastas singleton modelis: projekte turi būti 0 arba 1 įrašas.
    """

    base_url = models.URLField(blank=True, default="")
    token = models.TextField(blank=True, default="")
    status_lang = models.CharField(max_length=8, blank=True, default="lt")

    sender_name = models.CharField(max_length=80, blank=True, default="")
    sender_phone = models.CharField(max_length=40, blank=True, default="")
    sender_street = models.CharField(max_length=120, blank=True, default="")
    sender_city = models.CharField(max_length=80, blank=True, default="")
    sender_postal_code = models.CharField(
        max_length=20, blank=True, default="")
    sender_country = models.CharField(max_length=2, blank=True, default="LT")

    payer_code = models.CharField(max_length=64, blank=True, default="")
    service_alias_courier = models.CharField(
        max_length=120, blank=True, default="")
    service_alias_locker = models.CharField(
        max_length=120, blank=True, default="")

    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "DPD config"
        verbose_name_plural = "DPD config"

    def __str__(self) -> str:
        return "DPD config"

    @classmethod
    def get_solo(cls) -> "DpdConfig":
        obj = cls.objects.order_by("id").first()
        if obj:
            return obj
        return cls.objects.create()
