from django.db import migrations


def disable(apps, schema_editor):
    PeriodicTask = apps.get_model("django_celery_beat", "PeriodicTask")
    PeriodicTask.objects.filter(name="unisend.sync_terminals").update(enabled=False)


class Migration(migrations.Migration):
    dependencies = [
        ("unisend", "0002_rename_unisend_ter_country_5bd6f0_idx_unisend_uni_country_83fd41_idx_and_more"),
        ("api", "0004_seed_periodic_tasks"),
        ("django_celery_beat", "0019_alter_periodictasks_options"),
    ]

    operations = [migrations.RunPython(disable, migrations.RunPython.noop)]
