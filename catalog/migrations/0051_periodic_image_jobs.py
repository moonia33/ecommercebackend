"""PeriodicTask irasai nuotrauku pipeline'ui (django-celery-beat).

Po kiekvieno importo nuotraukos lieka INGEST_PENDING — sitie job'ai
doroja backlog'a ir dogeneruoja trukstamas rendition'as, kad nereiketu
paleisti komandu ranka.
"""

from django.db import migrations


def create_tasks(apps, schema_editor):
    CrontabSchedule = apps.get_model("django_celery_beat", "CrontabSchedule")
    PeriodicTask = apps.get_model("django_celery_beat", "PeriodicTask")

    cron_15min, _ = CrontabSchedule.objects.get_or_create(
        minute="*/15", hour="*", day_of_week="*", day_of_month="*",
        month_of_year="*",
    )
    cron_hourly, _ = CrontabSchedule.objects.get_or_create(
        minute="17", hour="*", day_of_week="*", day_of_month="*",
        month_of_year="*",
    )

    PeriodicTask.objects.update_or_create(
        name="catalog: ingest pending product images",
        defaults={
            "task": "catalog.tasks.ingest_pending_images_task",
            "crontab": cron_15min,
            "enabled": True,
        },
    )
    PeriodicTask.objects.update_or_create(
        name="catalog: regenerate missing image renditions",
        defaults={
            "task": "catalog.tasks.regenerate_image_renditions_task",
            "crontab": cron_hourly,
            "enabled": True,
        },
    )


def remove_tasks(apps, schema_editor):
    PeriodicTask = apps.get_model("django_celery_beat", "PeriodicTask")
    PeriodicTask.objects.filter(
        name__in=[
            "catalog: ingest pending product images",
            "catalog: regenerate missing image renditions",
        ]
    ).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("catalog", "0050_brand_logo_thumb"),
        ("django_celery_beat", "0019_alter_periodictasks_options"),
    ]

    operations = [
        migrations.RunPython(create_tasks, remove_tasks),
    ]
