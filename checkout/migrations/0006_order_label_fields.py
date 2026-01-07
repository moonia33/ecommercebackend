from __future__ import annotations

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("checkout", "0005_order_pickup_locker"),
    ]

    operations = [
        migrations.AddField(
            model_name="order",
            name="carrier_shipment_id",
            field=models.CharField(blank=True, default="", max_length=80),
        ),
        migrations.AddField(
            model_name="order",
            name="shipping_label_generated_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="order",
            name="shipping_label_pdf",
            field=models.FileField(blank=True, null=True,
                                   upload_to="shipping_labels/%Y/%m/"),
        ),
    ]
