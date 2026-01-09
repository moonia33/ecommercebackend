from __future__ import annotations

import django.db.models.deletion
from django.db import migrations, models
from django.db.models import Q


class Migration(migrations.Migration):

    dependencies = [
        ("catalog", "0014_inventoryitem_offer_fields"),
        ("checkout", "0014_inventoryallocation"),
    ]

    operations = [
        migrations.AddField(
            model_name="cartitem",
            name="offer",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="cart_items",
                to="catalog.inventoryitem",
            ),
        ),
        migrations.RemoveConstraint(
            model_name="cartitem",
            name="uniq_cart_variant",
        ),
        migrations.AddConstraint(
            model_name="cartitem",
            constraint=models.UniqueConstraint(
                condition=Q(offer__isnull=True),
                fields=("cart", "variant"),
                name="uniq_cart_variant_when_offer_null",
            ),
        ),
        migrations.AddConstraint(
            model_name="cartitem",
            constraint=models.UniqueConstraint(
                condition=Q(offer__isnull=False),
                fields=("cart", "offer"),
                name="uniq_cart_offer_when_offer_present",
            ),
        ),
        migrations.AddField(
            model_name="orderline",
            name="offer",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="order_lines",
                to="catalog.inventoryitem",
            ),
        ),
    ]
