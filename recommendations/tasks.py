from celery import shared_task


@shared_task
def rebuild_product_cooccurrence_task() -> dict:
    from .services import rebuild_cooccurrence, rebuild_momentum

    stats = rebuild_cooccurrence()
    stats["momentum"] = rebuild_momentum()
    return stats


SNAPSHOT_LOCK_KEY = "recommendations:build_snapshots:lock"
SNAPSHOT_LOCK_TTL = 6 * 3600


@shared_task
def build_recommendation_snapshots_task() -> dict:
    """Naktinis snapshot'ų rebuild — po cooccur/momentum rebuild'o.

    Lock'as: broker'is (Redis visibility_timeout 1 h) ilgą task'ą
    perduoda dar kartą — antra kopija neturi startuoti lygiagrečiai.
    """
    from django.core.cache import cache

    from .services import build_snapshots

    if not cache.add(SNAPSHOT_LOCK_KEY, 1, SNAPSHOT_LOCK_TTL):
        return {"skipped": "already running"}
    try:
        return build_snapshots()
    finally:
        cache.delete(SNAPSHOT_LOCK_KEY)
