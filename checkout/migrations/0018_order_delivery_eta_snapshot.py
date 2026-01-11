from __future__ import annotations

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("checkout", "0017_rename_checkout_or_order_i_3f8b8d_idx_checkout_or_order_i_249c73_idx_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="order",
            name="delivery_eta_kind",
            field=models.CharField(blank=True, default="", max_length=20),
        ),
        migrations.AddField(
            model_name="order",
            name="delivery_eta_rule_code",
            field=models.CharField(blank=True, default="", max_length=80),
        ),
        migrations.AddField(
            model_name="order",
            name="delivery_eta_source",
            field=models.CharField(blank=True, default="", max_length=120),
        ),
        migrations.AddField(
            model_name="order",
            name="delivery_max_date",
            field=models.DateField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="order",
            name="delivery_min_date",
            field=models.DateField(blank=True, null=True),
        ),
    ]
