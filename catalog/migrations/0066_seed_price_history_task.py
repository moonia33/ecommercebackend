from __future__ import annotations

from django.db import migrations


def seed(apps, schema_editor):
    CrontabSchedule = apps.get_model("django_celery_beat", "CrontabSchedule")
    PeriodicTask = apps.get_model("django_celery_beat", "PeriodicTask")
    every_30m, _ = CrontabSchedule.objects.get_or_create(
        minute="*/30", hour="*", day_of_week="*", day_of_month="*",
        month_of_year="*", timezone="Europe/Vilnius",
    )
    PeriodicTask.objects.update_or_create(
        name="catalog.record_variant_prices",
        defaults={
            "task": "catalog.tasks.record_variant_prices_task",
            "crontab": every_30m,
            "description": "Omnibus: final public price changes -> VariantPriceHistory",
            "enabled": True,
        },
    )


def unseed(apps, schema_editor):
    apps.get_model("django_celery_beat", "PeriodicTask").objects.filter(
        name="catalog.record_variant_prices"
    ).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("catalog", "0065_variant_price_history"),
        ("django_celery_beat", "0019_alter_periodictasks_options"),
    ]

    operations = [migrations.RunPython(seed, unseed)]
