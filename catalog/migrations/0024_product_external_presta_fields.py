from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("catalog", "0023_brand_external_presta_id"),
    ]

    operations = [
        migrations.AddField(
            model_name="product",
            name="external_presta_id",
            field=models.CharField(
                blank=True,
                db_index=True,
                help_text="Original PrestaShop id_product used for import mapping.",
                max_length=32,
                null=True,
                unique=True,
            ),
        ),
        migrations.AddField(
            model_name="variant",
            name="external_presta_combination_id",
            field=models.CharField(
                blank=True,
                db_index=True,
                help_text="Original PrestaShop id_product_attribute (combination).",
                max_length=32,
                null=True,
                unique=True,
            ),
        ),
    ]
