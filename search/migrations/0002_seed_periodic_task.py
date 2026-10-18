from __future__ import annotations

from django.db import migrations


def seed_periodic_tasks(apps, schema_editor):
    CrontabSchedule = apps.get_model("django_celery_beat", "CrontabSchedule")
    PeriodicTask = apps.get_model("django_celery_beat", "PeriodicTask")

    daily_0445, _ = CrontabSchedule.objects.get_or_create(
        minute="45", hour="4", timezone="Europe/Vilnius"
    )
    daily_0500, _ = CrontabSchedule.objects.get_or_create(
        minute="0", hour="5", timezone="Europe/Vilnius"
    )
    tasks = [
        {
            "name": "search.sync_curation",
            "task": "search.tasks.sync_search_curation_task",
            "crontab": daily_0445,
            "description": "Sync admin-managed synonyms/overrides into Typesense",
        },
        {
            "name": "search.reindex_products",
            "task": "search.tasks.reindex_products_task",
            "crontab": daily_0500,
            "description": "Full Typesense products reindex (drift healing)",
        },
    ]
    for spec in tasks:
        PeriodicTask.objects.update_or_create(
            name=spec["name"],
            defaults={**spec, "enabled": True},
        )


def unseed_periodic_tasks(apps, schema_editor):
    PeriodicTask = apps.get_model("django_celery_beat", "PeriodicTask")
    PeriodicTask.objects.filter(
        name__in=["search.sync_curation", "search.reindex_products"]
    ).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("search", "0001_initial"),
        ("django_celery_beat", "0019_alter_periodictasks_options"),
    ]

    operations = [
        migrations.RunPython(seed_periodic_tasks, unseed_periodic_tasks),
    ]
