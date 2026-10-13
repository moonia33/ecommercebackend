from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("catalog", "0022_category_external_presta_id"),
    ]

    operations = [
        migrations.AddField(
            model_name="brand",
            name="external_presta_id",
            field=models.CharField(
                blank=True,
                db_index=True,
                help_text="Original PrestaShop id_manufacturer used for import mapping.",
                max_length=32,
                null=True,
                unique=True,
            ),
        ),
    ]
