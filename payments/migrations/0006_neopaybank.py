from __future__ import annotations

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("payments", "0005_paymentmethod_image"),
    ]

    operations = [
        migrations.CreateModel(
            name="NeopayBank",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("country_code", models.CharField(max_length=2)),
                ("bic", models.CharField(max_length=32)),
                ("name", models.CharField(blank=True, default="", max_length=200)),
                ("logo_url", models.URLField(blank=True, default="")),
                ("is_operating", models.BooleanField(default=True)),
                ("is_enabled", models.BooleanField(default=True)),
                ("sort_order", models.IntegerField(default=0)),
                ("raw", models.JSONField(blank=True, default=dict)),
                ("last_synced_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={
                "ordering": ["country_code", "sort_order", "name", "bic"],
            },
        ),
        migrations.AddIndex(
            model_name="neopaybank",
            index=models.Index(fields=["country_code", "is_enabled", "sort_order"], name="payments_neop_country__96a492_idx"),
        ),
        migrations.AddIndex(
            model_name="neopaybank",
            index=models.Index(fields=["bic"], name="payments_neop_bic_2ed0bd_idx"),
        ),
        migrations.AddConstraint(
            model_name="neopaybank",
            constraint=models.UniqueConstraint(fields=("country_code", "bic"), name="uniq_neopay_bank_country_bic"),
        ),
    ]
