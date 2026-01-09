from __future__ import annotations

from django.db import migrations, models
from django.db.models import Q


class Migration(migrations.Migration):

    dependencies = [
        ("catalog", "0013_remove_product_price_eur"),
    ]

    operations = [
        migrations.AddField(
            model_name="inventoryitem",
            name="condition_grade",
            field=models.CharField(
                choices=[
                    ("NEW", "New"),
                    ("RETURNED_A", "Returned (A)"),
                    ("DAMAGED_B", "Damaged (B)"),
                ],
                default="NEW",
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name="inventoryitem",
            name="offer_visibility",
            field=models.CharField(
                choices=[("NORMAL", "Normal"), ("OUTLET", "Outlet")],
                default="NORMAL",
                max_length=10,
            ),
        ),
        migrations.AddField(
            model_name="inventoryitem",
            name="offer_priority",
            field=models.IntegerField(default=0),
        ),
        migrations.AddField(
            model_name="inventoryitem",
            name="offer_label",
            field=models.CharField(blank=True, max_length=255),
        ),
        migrations.AddField(
            model_name="inventoryitem",
            name="offer_price_override_eur",
            field=models.DecimalField(
                blank=True, decimal_places=2, max_digits=12, null=True
            ),
        ),
        migrations.AddField(
            model_name="inventoryitem",
            name="offer_discount_percent",
            field=models.PositiveSmallIntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="inventoryitem",
            name="allow_additional_promotions",
            field=models.BooleanField(default=False),
        ),
        migrations.AddConstraint(
            model_name="inventoryitem",
            constraint=models.CheckConstraint(
                check=Q(offer_price_override_eur__gte=0)
                | Q(offer_price_override_eur__isnull=True),
                name="chk_inventory_offer_price_override_eur_gte_0",
            ),
        ),
        migrations.AddConstraint(
            model_name="inventoryitem",
            constraint=models.CheckConstraint(
                check=Q(offer_discount_percent__gte=0, offer_discount_percent__lte=100)
                | Q(offer_discount_percent__isnull=True),
                name="chk_inventory_offer_discount_percent_0_100",
            ),
        ),
    ]
