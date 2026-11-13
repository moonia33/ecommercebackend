from celery import shared_task


@shared_task
def rebuild_product_cooccurrence_task() -> dict:
    from .services import rebuild_cooccurrence, rebuild_momentum

    stats = rebuild_cooccurrence()
    stats["momentum"] = rebuild_momentum()
    return stats
