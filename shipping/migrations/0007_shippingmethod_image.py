from __future__ import annotations

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("shipping", "0006_seed_shipping_countries"),
    ]

    operations = [
        migrations.AddField(
            model_name="shippingmethod",
            name="image",
            field=models.ImageField(blank=True, null=True, upload_to="shipping_methods/%Y/%m/"),
        ),
    ]
