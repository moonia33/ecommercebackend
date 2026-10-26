from __future__ import annotations

from django.db import migrations


def seed_reindex_task(apps, schema_editor):
    CrontabSchedule = apps.get_model("django_celery_beat", "CrontabSchedule")
    PeriodicTask = apps.get_model("django_celery_beat", "PeriodicTask")

    nightly, _ = CrontabSchedule.objects.get_or_create(
        minute="15", hour="3", timezone="Europe/Vilnius"
    )

    # Kasnaktinė pilna reindex'reconciliacija: bulk_update takai ir
    # galimi task'ų praleidimai nenorėtų kaupti Typesense drift'o.
    PeriodicTask.objects.get_or_create(
        name="search.full_reindex",
        defaults={
            "task": "search.tasks.reindex_products_task",
            "crontab": nightly,
            "description": "Full Typesense products reindex (reconcile drift)",
        },
    )


def unseed_reindex_task(apps, schema_editor):
    PeriodicTask = apps.get_model("django_celery_beat", "PeriodicTask")
    PeriodicTask.objects.filter(name="search.full_reindex").delete()


class Migration(migrations.Migration):
    dependencies = [
        ("api", "0004_seed_periodic_tasks"),
        ("django_celery_beat", "0019_alter_periodictasks_options"),
    ]

    operations = [
        migrations.RunPython(seed_reindex_task, unseed_reindex_task),
    ]
