from django.db import migrations


def seed_default_variants(apps, schema_editor):
    Product = apps.get_model("catalog", "Product")
    Variant = apps.get_model("catalog", "Variant")

    for product in Product.objects.all().iterator():
        # Create one default Variant per Product (MVP compatibility)
        if Variant.objects.filter(product_id=product.id).exists():
            continue
        Variant.objects.create(
            product_id=product.id,
            sku=product.sku,
            barcode="",
            name="",
            price_eur=product.price_eur,
            is_active=product.is_active,
        )


class Migration(migrations.Migration):
    dependencies = [
        ("catalog", "0002_feature_optiontype_productgroup_featurevalue_and_more"),
    ]

    operations = [
        migrations.RunPython(seed_default_variants, migrations.RunPython.noop),
    ]
