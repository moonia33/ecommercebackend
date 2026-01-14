from __future__ import annotations

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("payments", "0004_neopayconfig_force_bank_bic_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="paymentmethod",
            name="image",
            field=models.ImageField(blank=True, null=True, upload_to="payment_methods/%Y/%m/"),
        ),
    ]
