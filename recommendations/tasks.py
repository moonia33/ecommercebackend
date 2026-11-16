from celery import shared_task


@shared_task
def rebuild_product_cooccurrence_task() -> dict:
    from .services import rebuild_cooccurrence, rebuild_momentum

    stats = rebuild_cooccurrence()
    stats["momentum"] = rebuild_momentum()
    return stats


SNAPSHOT_LOCK_KEY = "recommendations:build_snapshots:lock"
SNAPSHOT_BUILT_KEY = "recommendations:build_snapshots:started_at"
# Build'as trunka ~30 min; TTL riboja, kiek ilgai miręs worker'is
# (OOM kill) blokuoja pakartojimą.
SNAPSHOT_LOCK_TTL = 2 * 3600
SNAPSHOT_RETRY_COUNTDOWN = 30 * 60


@shared_task(bind=True, max_retries=5)
def build_recommendation_snapshots_task(self, requested_at=None) -> dict:
    """Naktinis snapshot'ų rebuild — po cooccur/momentum rebuild'o.

    Lock'as: broker'is (Redis visibility_timeout 1 h) ilgą task'ą
    perduoda dar kartą — antra kopija lygiagrečiai nestartuoja, o
    bando vėliau; jei tuo metu jau baigtas build'as, pradėtas po
    užklausos, jis jau padengia šią užklausą.
    """
    import time
    import uuid

    from django.core.cache import cache

    from .services import build_snapshots

    requested_at = requested_at or time.time()
    token = uuid.uuid4().hex
    if not cache.add(SNAPSHOT_LOCK_KEY, token, SNAPSHOT_LOCK_TTL):
        raise self.retry(
            countdown=SNAPSHOT_RETRY_COUNTDOWN,
            kwargs={"requested_at": requested_at},
        )
    try:
        last_started = cache.get(SNAPSHOT_BUILT_KEY)
        if last_started and last_started >= requested_at:
            return {"skipped": "already built"}
        started = time.time()
        stats = build_snapshots()
        cache.set(SNAPSHOT_BUILT_KEY, started, None)
        return stats
    finally:
        if cache.get(SNAPSHOT_LOCK_KEY) == token:
            cache.delete(SNAPSHOT_LOCK_KEY)
