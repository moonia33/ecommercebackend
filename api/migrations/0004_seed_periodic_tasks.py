from __future__ import annotations

from django.db import migrations


def seed_periodic_tasks(apps, schema_editor):
    IntervalSchedule = apps.get_model("django_celery_beat", "IntervalSchedule")
    CrontabSchedule = apps.get_model("django_celery_beat", "CrontabSchedule")
    PeriodicTask = apps.get_model("django_celery_beat", "PeriodicTask")

    every_5_min, _ = IntervalSchedule.objects.get_or_create(
        every=5, period="minutes"
    )
    every_15_min, _ = IntervalSchedule.objects.get_or_create(
        every=15, period="minutes"
    )
    every_30_min, _ = IntervalSchedule.objects.get_or_create(
        every=30, period="minutes"
    )
    daily_0300, _ = CrontabSchedule.objects.get_or_create(
        minute="0", hour="3", timezone="Europe/Vilnius"
    )
    daily_0330, _ = CrontabSchedule.objects.get_or_create(
        minute="30", hour="3", timezone="Europe/Vilnius"
    )

    tasks = [
        {
            "name": "checkout.expire_inventory_reservations",
            "task": "checkout.tasks.expire_inventory_reservations_task",
            "interval": every_5_min,
            "description": "Release inventory for orders stuck in pending_payment",
        },
        {
            "name": "notifications.retry_failed_emails",
            "task": "notifications.tasks.retry_failed_emails_task",
            "interval": every_15_min,
            "description": "Requeue failed outbound emails",
        },
        {
            "name": "dpd.sync_statuses",
            "task": "dpd.tasks.dpd_sync_statuses_task",
            "interval": every_30_min,
            "description": "Poll DPD tracking statuses for shipped orders",
        },
        {
            "name": "dpd.sync_lockers",
            "task": "dpd.tasks.dpd_sync_lockers_task",
            "crontab": daily_0300,
            "description": "Refresh DPD locker directory",
        },
        {
            "name": "unisend.sync_terminals",
            "task": "unisend.tasks.unisend_sync_terminals_task",
            "crontab": daily_0330,
            "description": "Refresh Unisend/LPExpress terminal directory",
        },
    ]

    for spec in tasks:
        name = spec.pop("name")
        PeriodicTask.objects.get_or_create(name=name, defaults=spec)


def unseed_periodic_tasks(apps, schema_editor):
    PeriodicTask = apps.get_model("django_celery_beat", "PeriodicTask")
    PeriodicTask.objects.filter(
        name__in=[
            "checkout.expire_inventory_reservations",
            "notifications.retry_failed_emails",
            "dpd.sync_statuses",
            "dpd.sync_lockers",
            "unisend.sync_terminals",
        ]
    ).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("api", "0003_shoplanguage_cleanup_supported_languages"),
        ("django_celery_beat", "0019_alter_periodictasks_options"),
    ]

    operations = [
        migrations.RunPython(seed_periodic_tasks, unseed_periodic_tasks),
    ]
