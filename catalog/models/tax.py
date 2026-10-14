from __future__ import annotations

from django.db import models


class TaxClass(models.Model):
    code = models.SlugField(max_length=50, unique=True)
    name = models.CharField(max_length=255)
    is_active = models.BooleanField(default=True)
    sort_order = models.IntegerField(default=0)

    class Meta:
        ordering = ["sort_order", "code"]

    def __str__(self) -> str:
        return self.code


class TaxRate(models.Model):
    tax_class = models.ForeignKey(
        TaxClass, on_delete=models.CASCADE, related_name="rates"
    )

    # ISO 3166-1 alpha-2 (e.g. LT)
    country_code = models.CharField(max_length=2)

    # VAT rate as a fraction: 0.21 == 21%
    rate = models.DecimalField(max_digits=6, decimal_places=5)

    valid_from = models.DateField()
    valid_to = models.DateField(null=True, blank=True)

    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["country_code", "tax_class__code", "-valid_from"]
        constraints = [
            models.UniqueConstraint(
                fields=["tax_class", "country_code", "valid_from"],
                name="uniq_tax_rate_effective",
            ),
            models.CheckConstraint(
                check=models.Q(rate__gte=0) & models.Q(rate__lte=1),
                name="chk_tax_rate_between_0_and_1",
            ),
            models.CheckConstraint(
                check=models.Q(valid_to__gte=models.F("valid_from"))
                | models.Q(valid_to__isnull=True),
                name="chk_tax_rate_valid_to_gte_valid_from",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.country_code}:{self.tax_class.code}={self.rate}"
