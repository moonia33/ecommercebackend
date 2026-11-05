from __future__ import annotations

from django.db import migrations


def seed(apps, schema_editor):
    CrontabSchedule = apps.get_model("django_celery_beat", "CrontabSchedule")
    PeriodicTask = apps.get_model("django_celery_beat", "PeriodicTask")
    daily_0350, _ = CrontabSchedule.objects.get_or_create(
        minute="50", hour="3", day_of_week="*", day_of_month="*",
        month_of_year="*", timezone="Europe/Vilnius",
    )
    PeriodicTask.objects.update_or_create(
        name="catalog.apply_badge_rules",
        defaults={
            "task": "catalog.tasks.apply_badge_rules_task",
            "crontab": daily_0350,
            "description": "Apply BadgeRule rules and reindex products with expiring badges",
            "enabled": True,
        },
    )


def unseed(apps, schema_editor):
    apps.get_model("django_celery_beat", "PeriodicTask").objects.filter(
        name="catalog.apply_badge_rules"
    ).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("catalog", "0058_product_badges"),
        ("django_celery_beat", "0019_alter_periodictasks_options"),
    ]

    operations = [migrations.RunPython(seed, unseed)]
