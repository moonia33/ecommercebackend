from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("catalog", "0021_categorytranslation_and_mptt"),
    ]

    operations = [
        migrations.AddField(
            model_name="category",
            name="external_presta_id",
            field=models.CharField(
                blank=True,
                db_index=True,
                help_text="Original PrestaShop id_category used for import mapping.",
                max_length=32,
                null=True,
                unique=True,
            ),
        ),
    ]
