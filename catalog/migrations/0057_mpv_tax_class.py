import datetime
from decimal import Decimal

from django.db import migrations


def create_mpv_tax_class(apps, schema_editor):
    TaxClass = apps.get_model("catalog", "TaxClass")
    TaxRate = apps.get_model("catalog", "TaxRate")
    tc, _ = TaxClass.objects.get_or_create(
        code="mpv",
        defaults={
            "name": "Dovanų kortelės / multi-purpose voucher (0% PVM)",
            "is_active": True,
            "sort_order": 90,
        },
    )
    TaxRate.objects.get_or_create(
        tax_class=tc,
        country_code="LT",
        valid_from=datetime.date(2020, 1, 1),
        defaults={"rate": Decimal("0"), "is_active": True},
    )


def remove_mpv_tax_class(apps, schema_editor):
    TaxClass = apps.get_model("catalog", "TaxClass")
    TaxClass.objects.filter(code="mpv").delete()


class Migration(migrations.Migration):

    dependencies = [
        ("catalog", "0056_inventoryadjustment"),
    ]

    operations = [
        migrations.RunPython(create_mpv_tax_class, remove_mpv_tax_class),
    ]
