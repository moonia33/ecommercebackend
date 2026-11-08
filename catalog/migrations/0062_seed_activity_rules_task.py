from __future__ import annotations

from django.db import migrations


def seed(apps, schema_editor):
    CrontabSchedule = apps.get_model("django_celery_beat", "CrontabSchedule")
    PeriodicTask = apps.get_model("django_celery_beat", "PeriodicTask")
    daily_0410, _ = CrontabSchedule.objects.get_or_create(
        minute="10", hour="4", day_of_week="*", day_of_month="*",
        month_of_year="*", timezone="Europe/Vilnius",
    )
    PeriodicTask.objects.update_or_create(
        name="catalog.apply_activity_rules",
        defaults={
            "task": "catalog.tasks.apply_activity_rules_task",
            "crontab": daily_0410,
            "description": "Apply ActivityRule rules (new imported products get activities)",
            "enabled": True,
        },
    )


def unseed(apps, schema_editor):
    apps.get_model("django_celery_beat", "PeriodicTask").objects.filter(
        name="catalog.apply_activity_rules"
    ).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("catalog", "0061_activities"),
        ("django_celery_beat", "0019_alter_periodictasks_options"),
    ]

    operations = [migrations.RunPython(seed, unseed)]
