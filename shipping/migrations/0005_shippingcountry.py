from __future__ import annotations

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("shipping", "0004_deliveryrule_holiday"),
    ]

    operations = [
        migrations.CreateModel(
            name="ShippingCountry",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("code", models.CharField(max_length=2, unique=True)),
                ("is_active", models.BooleanField(default=True)),
                ("sort_order", models.IntegerField(default=0)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={
                "ordering": ["sort_order", "code"],
            },
        ),
        migrations.CreateModel(
            name="ShippingCountryTranslation",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("language_code", models.CharField(max_length=8)),
                ("name", models.CharField(blank=True, default="", max_length=255)),
                (
                    "shipping_country",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="translations",
                        to="shipping.shippingcountry",
                    ),
                ),
            ],
            options={
                "indexes": [models.Index(fields=["shipping_country", "language_code"], name="shipping_shi_shipping_21fe3e_idx")],
                "unique_together": {("shipping_country", "language_code")},
            },
        ),
    ]
