from __future__ import annotations

from decimal import Decimal

from django.db import migrations


def add_lpexpress_courier(apps, schema_editor):
    ShippingMethod = apps.get_model("shipping", "ShippingMethod")
    ShippingRate = apps.get_model("shipping", "ShippingRate")

    d = {
        "code": "lpexpress_courier",
        "name": "LPExpress kurjeris (Unisend)",
        "carrier_code": "lpexpress",
        "requires_pickup_point": False,
        "sort_order": 15,
    }

    m, _ = ShippingMethod.objects.get_or_create(
        code=d["code"],
        defaults={
            "name": d["name"],
            "carrier_code": d["carrier_code"],
            "requires_pickup_point": d["requires_pickup_point"],
            "is_active": True,
            "sort_order": d["sort_order"],
        },
    )

    # Keep updated if already existed.
    if (
        m.name != d["name"]
        or m.carrier_code != d["carrier_code"]
        or m.requires_pickup_point != d["requires_pickup_point"]
        or m.sort_order != d["sort_order"]
    ):
        m.name = d["name"]
        m.carrier_code = d["carrier_code"]
        m.requires_pickup_point = d["requires_pickup_point"]
        m.is_active = True
        m.sort_order = d["sort_order"]
        m.save(
            update_fields=[
                "name",
                "carrier_code",
                "requires_pickup_point",
                "is_active",
                "sort_order",
                "updated_at",
            ]
        )

    # Ensure there's at least an LT rate row (admin can adjust later)
    ShippingRate.objects.get_or_create(
        method_id=m.id,
        country_code="LT",
        defaults={"net_eur": Decimal("0.00"), "is_active": True},
    )


class Migration(migrations.Migration):

    dependencies = [
        ("shipping", "0002_seed_defaults"),
    ]

    operations = [
        migrations.RunPython(add_lpexpress_courier, migrations.RunPython.noop),
    ]
