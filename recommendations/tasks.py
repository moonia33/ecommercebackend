from celery import shared_task


@shared_task
def rebuild_product_cooccurrence_task() -> dict:
    from .services import rebuild_cooccurrence

    return rebuild_cooccurrence()
