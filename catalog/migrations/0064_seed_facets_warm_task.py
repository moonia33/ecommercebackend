from __future__ import annotations

from django.db import migrations


def seed(apps, schema_editor):
    CrontabSchedule = apps.get_model("django_celery_beat", "CrontabSchedule")
    PeriodicTask = apps.get_model("django_celery_beat", "PeriodicTask")
    every_4h, _ = CrontabSchedule.objects.get_or_create(
        minute="25", hour="*/4", day_of_week="*", day_of_month="*",
        month_of_year="*", timezone="Europe/Vilnius",
    )
    PeriodicTask.objects.update_or_create(
        name="catalog.warm_catalog_facets",
        defaults={
            "task": "catalog.tasks.warm_catalog_facets_task",
            "crontab": every_4h,
            "description": "Warm facets cache for every active category page (no filters)",
            "enabled": True,
        },
    )


def unseed(apps, schema_editor):
    apps.get_model("django_celery_beat", "PeriodicTask").objects.filter(
        name="catalog.warm_catalog_facets"
    ).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("catalog", "0063_category_image_renditions"),
        ("django_celery_beat", "0019_alter_periodictasks_options"),
    ]

    operations = [migrations.RunPython(seed, unseed)]
