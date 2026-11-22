from __future__ import annotations

from django.db import models
from django.db.models import Q


class VariantPriceHistory(models.Model):
    """Galutinės viešos variant'o kainos intervalas (Omnibus: mažiausia kaina per 30 d.).

    Eilutė kuriama tik pasikeitus kainai; `valid_to=NULL` – dabartinė kaina.
    `prior_unit_net` – mažiausia kaina per 30 d. iki `valid_from` (be šio intervalo),
    fiksuojama kuriant eilutę; NULL – istorijos nėra.
    """

    variant = models.ForeignKey(
        "catalog.Variant", on_delete=models.CASCADE, related_name="price_history"
    )
    shop = models.ForeignKey("api.Shop", on_delete=models.CASCADE, related_name="variant_price_history")
    channel = models.CharField(max_length=16, default="normal")
    unit_net = models.DecimalField(max_digits=12, decimal_places=2)
    prior_unit_net = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    valid_from = models.DateTimeField()
    valid_to = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["variant", "shop", "channel"],
                condition=Q(valid_to__isnull=True),
                name="uniq_open_variant_price",
            ),
        ]
        indexes = [
            models.Index(fields=["shop", "channel", "variant", "valid_to"]),
        ]

    def __str__(self) -> str:
        return f"{self.variant_id}@{self.shop_id}/{self.channel}: {self.unit_net} nuo {self.valid_from:%Y-%m-%d %H:%M}"
