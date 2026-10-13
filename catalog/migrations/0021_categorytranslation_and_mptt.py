from django.db import migrations, models
import django.db.models.deletion


def noop_rebuild_category_tree(apps, schema_editor):
    # Historical models in migrations use plain Manager, so mptt TreeManager
    # methods (e.g. rebuild()) are unavailable here. We rebuild after migrate
    # via runtime model manager: Category.objects.rebuild().
    return None


class Migration(migrations.Migration):
    dependencies = [
        ("catalog", "0020_enrichmentrule_enrichmentrun_enrichmentmatch"),
    ]

    operations = [
        migrations.AlterModelOptions(
            name="category",
            options={"ordering": ["tree_id", "lft"]},
        ),
        migrations.AddField(
            model_name="category",
            name="lft",
            field=models.PositiveIntegerField(db_index=True, default=0, editable=False),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name="category",
            name="rght",
            field=models.PositiveIntegerField(db_index=True, default=0, editable=False),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name="category",
            name="tree_id",
            field=models.PositiveIntegerField(db_index=True, default=0, editable=False),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name="category",
            name="level",
            field=models.PositiveIntegerField(db_index=True, default=0, editable=False),
            preserve_default=False,
        ),
        migrations.CreateModel(
            name="CategoryTranslation",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("language_code", models.CharField(max_length=10)),
                ("name", models.CharField(blank=True, default="", max_length=200)),
                ("slug", models.SlugField(max_length=200)),
                ("description", models.TextField(blank=True, default="")),
                ("seo_title", models.CharField(blank=True, default="", max_length=255)),
                ("seo_description", models.CharField(blank=True, default="", max_length=320)),
                ("seo_keywords", models.CharField(blank=True, default="", max_length=255)),
                (
                    "category",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="translations",
                        to="catalog.category",
                    ),
                ),
            ],
        ),
        migrations.AddConstraint(
            model_name="categorytranslation",
            constraint=models.UniqueConstraint(fields=("category", "language_code"), name="uniq_category_translation"),
        ),
        migrations.AddConstraint(
            model_name="categorytranslation",
            constraint=models.UniqueConstraint(fields=("language_code", "slug"), name="uniq_category_translation_slug"),
        ),
        migrations.RunPython(noop_rebuild_category_tree, migrations.RunPython.noop),
    ]
