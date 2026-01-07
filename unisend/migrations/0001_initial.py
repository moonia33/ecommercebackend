from __future__ import annotations

from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies: list[tuple[str, str]] = []

    operations = [
        migrations.CreateModel(
            name="UnisendTerminal",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("terminal_id", models.CharField(max_length=32, unique=True)),
                ("country_code", models.CharField(blank=True, default="", max_length=2)),
                ("name", models.CharField(blank=True, default="", max_length=255)),
                ("locality", models.CharField(blank=True, default="", max_length=120)),
                ("street", models.CharField(blank=True, default="", max_length=255)),
                ("postal_code", models.CharField(blank=True, default="", max_length=32)),
                ("latitude", models.DecimalField(blank=True, decimal_places=6, max_digits=9, null=True)),
                ("longitude", models.DecimalField(blank=True, decimal_places=6, max_digits=9, null=True)),
                ("raw", models.JSONField(blank=True, default=dict)),
                ("is_active", models.BooleanField(default=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={
                "ordering": ["country_code", "locality", "name", "terminal_id"],
            },
        ),
        migrations.CreateModel(
            name="UnisendApiConfig",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("base_url", models.URLField(blank=True, default="")),
                ("username", models.CharField(blank=True, default="", max_length=120)),
                ("password", models.TextField(blank=True, default="")),
                ("client_system", models.CharField(blank=True, default="PUBLIC", max_length=40)),
                ("access_token", models.TextField(blank=True, default="")),
                ("refresh_token", models.TextField(blank=True, default="")),
                ("token_expires_at", models.DateTimeField(blank=True, null=True)),
                ("sender_name", models.CharField(blank=True, default="", max_length=120)),
                ("sender_email", models.CharField(blank=True, default="", max_length=200)),
                ("sender_phone", models.CharField(blank=True, default="", max_length=40)),
                ("sender_country", models.CharField(blank=True, default="LT", max_length=2)),
                ("sender_locality", models.CharField(blank=True, default="", max_length=120)),
                ("sender_postal_code", models.CharField(blank=True, default="", max_length=32)),
                ("sender_street", models.CharField(blank=True, default="", max_length=255)),
                ("sender_building", models.CharField(blank=True, default="", max_length=32)),
                ("sender_flat", models.CharField(blank=True, default="", max_length=32)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={
                "verbose_name": "Unisend config",
                "verbose_name_plural": "Unisend config",
            },
        ),
        migrations.AddIndex(
            model_name="unisendterminal",
            index=models.Index(fields=["country_code", "locality"], name="unisend_ter_country_5bd6f0_idx"),
        ),
        migrations.AddIndex(
            model_name="unisendterminal",
            index=models.Index(fields=["terminal_id"], name="unisend_ter_terminal_2d1c35_idx"),
        ),
    ]
