from __future__ import annotations

from django.conf import settings
from django.db import migrations


def seed_shipping_countries(apps, schema_editor):
    ShippingCountry = apps.get_model("shipping", "ShippingCountry")
    ShippingCountryTranslation = apps.get_model("shipping", "ShippingCountryTranslation")

    default_lang = (getattr(settings, "LANGUAGE_CODE", "") or "lt").split("-")[0].strip().lower() or "lt"

    lt, _ = ShippingCountry.objects.get_or_create(
        code="LT",
        defaults={"is_active": True, "sort_order": 10},
    )
    if not lt.is_active or lt.sort_order != 10:
        lt.is_active = True
        lt.sort_order = 10
        lt.save(update_fields=["is_active", "sort_order", "updated_at"])

    ShippingCountryTranslation.objects.get_or_create(
        shipping_country_id=lt.id,
        language_code=default_lang,
        defaults={"name": "Lietuva"},
    )


class Migration(migrations.Migration):

    dependencies = [
        ("shipping", "0005_shippingcountry"),
    ]

    operations = [
        migrations.RunPython(seed_shipping_countries, migrations.RunPython.noop),
    ]
