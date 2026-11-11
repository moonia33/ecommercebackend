from django.db import migrations
from django.utils import timezone


def disable(apps, schema_editor):
    PeriodicTask = apps.get_model("django_celery_beat", "PeriodicTask")
    PeriodicTasks = apps.get_model("django_celery_beat", "PeriodicTasks")
    ShippingMethod = apps.get_model("shipping", "ShippingMethod")
    PeriodicTask.objects.filter(name="unisend.sync_terminals").update(enabled=False)
    # update() aplenkia signalą – DatabaseScheduler perskaito grafiką tik pasikeitus last_update.
    PeriodicTasks.objects.update_or_create(ident=1, defaults={"last_update": timezone.now()})
    ShippingMethod.objects.filter(carrier_code="lpexpress").update(is_active=False)


class Migration(migrations.Migration):
    dependencies = [
        ("unisend", "0002_rename_unisend_ter_country_5bd6f0_idx_unisend_uni_country_83fd41_idx_and_more"),
        ("api", "0004_seed_periodic_tasks"),
        ("django_celery_beat", "0019_alter_periodictasks_options"),
        ("shipping", "0002_seed_defaults"),
    ]

    operations = [migrations.RunPython(disable, migrations.RunPython.noop)]
