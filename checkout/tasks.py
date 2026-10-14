from celery import shared_task


@shared_task
def expire_inventory_reservations_task() -> int:
    from checkout.services import expire_pending_payment_reservations

    return expire_pending_payment_reservations()


@shared_task
def execute_ops_job_task(job_id: int):
    from checkout.models import OpsJob
    from checkout.ops_api import _execute_ops_job

    try:
        job = OpsJob.objects.select_related("actor_user").get(pk=job_id)
    except OpsJob.DoesNotExist:
        return

    _execute_ops_job(job, user=job.actor_user)
