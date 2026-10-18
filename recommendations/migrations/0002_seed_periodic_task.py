from __future__ import annotations

from django.db import migrations


def seed_periodic_tasks(apps, schema_editor):
    CrontabSchedule = apps.get_model("django_celery_beat", "CrontabSchedule")
    PeriodicTask = apps.get_model("django_celery_beat", "PeriodicTask")

    daily_0430, _ = CrontabSchedule.objects.get_or_create(
        minute="30", hour="4", timezone="Europe/Vilnius"
    )
    PeriodicTask.objects.update_or_create(
        name="recommendations.rebuild_cooccurrence",
        defaults={
            "task": "recommendations.tasks.rebuild_product_cooccurrence_task",
            "crontab": daily_0430,
            "description": "Rebuild product co-purchase matrix from paid orders",
            "enabled": True,
        },
    )


def unseed_periodic_tasks(apps, schema_editor):
    PeriodicTask = apps.get_model("django_celery_beat", "PeriodicTask")
    PeriodicTask.objects.filter(
        name="recommendations.rebuild_cooccurrence").delete()


class Migration(migrations.Migration):

    dependencies = [
        ("recommendations", "0001_initial"),
        ("django_celery_beat", "0019_alter_periodictasks_options"),
    ]

    operations = [
        migrations.RunPython(seed_periodic_tasks, unseed_periodic_tasks),
    ]
