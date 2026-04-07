from django.db import migrations


def rebuild_category_tree(apps, schema_editor):
    # NOTE: apps.get_model() returns a historical model with a plain Manager,
    # so it won't have MPTT's TreeManager methods like rebuild().
    # Using the real model here is safe because this migration runs immediately
    # after the MPTT fields are added.
    from catalog.models import Category

    Category.objects.rebuild()


class Migration(migrations.Migration):

    dependencies = [
        ("catalog", "0025_alter_category_options_category_level_category_lft_and_more"),
    ]

    operations = [
        migrations.RunPython(rebuild_category_tree, migrations.RunPython.noop),
    ]
