from celery import shared_task


@shared_task
def rebuild_product_cooccurrence_task() -> dict:
    from .services import rebuild_cooccurrence, rebuild_momentum

    stats = rebuild_cooccurrence()
    stats["momentum"] = rebuild_momentum()
    return stats


@shared_task
def build_recommendation_snapshots_task() -> dict:
    """Naktinis snapshot'ų rebuild — po cooccur/momentum rebuild'o."""
    from .services import build_snapshots

    return build_snapshots()
