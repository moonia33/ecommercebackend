from django.db import migrations
from django.db.models import F


def _backfill(apps, schema_editor):
    pairs = [
        ("Product", "external_presta_id"),
        ("Variant", "external_presta_combination_id"),
        ("Category", "external_presta_id"),
        ("Brand", "external_presta_id"),
    ]
    for model_name, field in pairs:
        model = apps.get_model("catalog", model_name)
        model.objects.filter(
            **{f"{field}__isnull": False}, import_source=""
        ).exclude(**{field: ""}).update(
            import_source="prestashop", import_code=F(field)
        )


class Migration(migrations.Migration):
    dependencies = [
        ("catalog", "0028_brand_import_code_brand_import_source_and_more"),
    ]

    operations = [
        migrations.RunPython(_backfill, reverse_code=migrations.RunPython.noop),
    ]
