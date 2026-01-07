from __future__ import annotations

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("dpd", "0001_initial"),
    ]

    operations = [
        migrations.CreateModel(
            name="DpdConfig",
            fields=[
                ("id", models.BigAutoField(auto_created=True,
                 primary_key=True, serialize=False, verbose_name="ID")),
                ("base_url", models.URLField(blank=True, default="")),
                ("token", models.TextField(blank=True, default="")),
                ("status_lang", models.CharField(
                    blank=True, default="lt", max_length=8)),
                ("sender_name", models.CharField(
                    blank=True, default="", max_length=80)),
                ("sender_phone", models.CharField(
                    blank=True, default="", max_length=40)),
                ("sender_street", models.CharField(
                    blank=True, default="", max_length=120)),
                ("sender_city", models.CharField(
                    blank=True, default="", max_length=80)),
                ("sender_postal_code", models.CharField(
                    blank=True, default="", max_length=20)),
                ("sender_country", models.CharField(
                    blank=True, default="LT", max_length=2)),
                ("payer_code", models.CharField(
                    blank=True, default="", max_length=64)),
                ("service_alias_courier", models.CharField(
                    blank=True, default="", max_length=120)),
                ("service_alias_locker", models.CharField(
                    blank=True, default="", max_length=120)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={
                "verbose_name": "DPD config",
                "verbose_name_plural": "DPD config",
            },
        ),
    ]
