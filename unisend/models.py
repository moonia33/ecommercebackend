from __future__ import annotations

from django.db import models


class UnisendTerminal(models.Model):
    terminal_id = models.CharField(max_length=32, unique=True)

    country_code = models.CharField(max_length=2, blank=True, default="")
    name = models.CharField(max_length=255, blank=True, default="")
    locality = models.CharField(max_length=120, blank=True, default="")
    street = models.CharField(max_length=255, blank=True, default="")
    postal_code = models.CharField(max_length=32, blank=True, default="")

    latitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    longitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)

    raw = models.JSONField(default=dict, blank=True)

    is_active = models.BooleanField(default=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["country_code", "locality", "name", "terminal_id"]
        indexes = [
            models.Index(fields=["country_code", "locality"]),
            models.Index(fields=["terminal_id"]),
        ]

    def __str__(self) -> str:
        parts = [p for p in [self.locality, self.name, self.street] if p]
        label = " - ".join(parts) if parts else self.terminal_id
        return f"{label} ({self.terminal_id})"


class UnisendApiConfig(models.Model):
    base_url = models.URLField(blank=True, default="")

    username = models.CharField(max_length=120, blank=True, default="")
    password = models.TextField(blank=True, default="")
    client_system = models.CharField(max_length=40, blank=True, default="PUBLIC")

    access_token = models.TextField(blank=True, default="")
    refresh_token = models.TextField(blank=True, default="")
    token_expires_at = models.DateTimeField(null=True, blank=True)

    sender_name = models.CharField(max_length=120, blank=True, default="")
    sender_email = models.CharField(max_length=200, blank=True, default="")
    sender_phone = models.CharField(max_length=40, blank=True, default="")
    sender_country = models.CharField(max_length=2, blank=True, default="LT")
    sender_locality = models.CharField(max_length=120, blank=True, default="")
    sender_postal_code = models.CharField(max_length=32, blank=True, default="")
    sender_street = models.CharField(max_length=255, blank=True, default="")
    sender_building = models.CharField(max_length=32, blank=True, default="")
    sender_flat = models.CharField(max_length=32, blank=True, default="")

    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Unisend config"
        verbose_name_plural = "Unisend config"

    def __str__(self) -> str:
        return "Unisend config"

    @classmethod
    def get_solo(cls) -> "UnisendApiConfig":
        obj = cls.objects.order_by("id").first()
        if obj:
            return obj
        return cls.objects.create()
