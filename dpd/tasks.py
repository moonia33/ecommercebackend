from celery import shared_task
from django.core.management import call_command


@shared_task
def dpd_sync_statuses_task() -> None:
    call_command("dpd_sync_statuses")


@shared_task
def dpd_sync_lockers_task() -> None:
    call_command("dpd_sync_lockers", "--deactivate-missing")
