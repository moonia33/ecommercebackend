from __future__ import annotations

from decimal import Decimal

from django.db import migrations


def seed_defaults(apps, schema_editor):
    ShippingMethod = apps.get_model("shipping", "ShippingMethod")
    ShippingRate = apps.get_model("shipping", "ShippingRate")

    defaults = [
        {
            "code": "lpexpress",
            "name": "LPExpress (Unisend)",
            "carrier_code": "lpexpress",
            "requires_pickup_point": True,
            "sort_order": 10,
        },
        {
            "code": "dpd_locker",
            "name": "DPD paštomatas",
            "carrier_code": "dpd",
            "requires_pickup_point": True,
            "sort_order": 20,
        },
        {
            "code": "dpd_courier",
            "name": "DPD kurjeris",
            "carrier_code": "dpd",
            "requires_pickup_point": False,
            "sort_order": 30,
        },
    ]

    for d in defaults:
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
        if m.name != d["name"]:
            m.name = d["name"]
            m.carrier_code = d["carrier_code"]
            m.requires_pickup_point = d["requires_pickup_point"]
            m.is_active = True
            m.sort_order = d["sort_order"]
            m.save(update_fields=[
                "name",
                "carrier_code",
                "requires_pickup_point",
                "is_active",
                "sort_order",
                "updated_at",
            ])

        # Ensure there's at least an LT rate row (admin can adjust later)
        ShippingRate.objects.get_or_create(
            method_id=m.id,
            country_code="LT",
            defaults={"net_eur": Decimal("0.00"), "is_active": True},
        )


class Migration(migrations.Migration):

    dependencies = [
        ("shipping", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(seed_defaults, migrations.RunPython.noop),
    ]
