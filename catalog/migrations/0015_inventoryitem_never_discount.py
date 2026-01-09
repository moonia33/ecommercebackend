from __future__ import annotations

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("catalog", "0014_inventoryitem_offer_fields"),
    ]

    operations = [
        migrations.AddField(
            model_name="inventoryitem",
            name="never_discount",
            field=models.BooleanField(default=False),
        ),
    ]
