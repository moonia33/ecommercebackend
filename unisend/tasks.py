from celery import shared_task
from django.core.management import call_command


@shared_task
def unisend_sync_terminals_task() -> None:
    call_command("unisend_sync_terminals", "--deactivate-missing")
