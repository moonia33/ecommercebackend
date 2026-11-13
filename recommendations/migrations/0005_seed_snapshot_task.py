from __future__ import annotations

from django.db import migrations


def seed_periodic_tasks(apps, schema_editor):
    CrontabSchedule = apps.get_model("django_celery_beat", "CrontabSchedule")
    PeriodicTask = apps.get_model("django_celery_beat", "PeriodicTask")

    # Po cooccur+momentum rebuild'o (04:30) — snapshot'ai naudoja šviežią matricą.
    daily_0545, _ = CrontabSchedule.objects.get_or_create(
        minute="45", hour="5", timezone="Europe/Vilnius"
    )
    PeriodicTask.objects.update_or_create(
        name="recommendations.build_snapshots",
        defaults={
            "task": "recommendations.tasks.build_recommendation_snapshots_task",
            "crontab": daily_0545,
            "description": "Precompute recommendation candidate lists per product x kind",
            "enabled": True,
        },
    )


def unseed_periodic_tasks(apps, schema_editor):
    PeriodicTask = apps.get_model("django_celery_beat", "PeriodicTask")
    PeriodicTask.objects.filter(
        name="recommendations.build_snapshots").delete()


class Migration(migrations.Migration):

    dependencies = [
        ("recommendations", "0004_recommendationsnapshot"),
        ("django_celery_beat", "0019_alter_periodictasks_options"),
    ]

    operations = [
        migrations.RunPython(seed_periodic_tasks, unseed_periodic_tasks),
    ]
