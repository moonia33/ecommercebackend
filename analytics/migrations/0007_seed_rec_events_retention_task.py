from __future__ import annotations

from django.db import migrations


def seed_periodic_tasks(apps, schema_editor):
    CrontabSchedule = apps.get_model("django_celery_beat", "CrontabSchedule")
    PeriodicTask = apps.get_model("django_celery_beat", "PeriodicTask")

    daily_0500, _ = CrontabSchedule.objects.get_or_create(
        minute="0", hour="5", timezone="Europe/Vilnius"
    )
    PeriodicTask.objects.update_or_create(
        name="analytics.prune_rec_events",
        defaults={
            "task": "analytics.tasks.prune_rec_events_task",
            "crontab": daily_0500,
            "description": "Delete recommendation impression/click events older than REC_EVENTS_RETENTION_DAYS",
            "enabled": True,
        },
    )


def unseed_periodic_tasks(apps, schema_editor):
    PeriodicTask = apps.get_model("django_celery_beat", "PeriodicTask")
    PeriodicTask.objects.filter(
        name="analytics.prune_rec_events").delete()


class Migration(migrations.Migration):

    dependencies = [
        ("analytics", "0006_alter_analyticsevent_name"),
        ("django_celery_beat", "0019_alter_periodictasks_options"),
    ]

    operations = [
        migrations.RunPython(seed_periodic_tasks, unseed_periodic_tasks),
    ]
