from celery import shared_task

from django.conf import settings
from django.utils import timezone

from .models import AnalyticsEvent

REC_EVENT_NAMES = (
    AnalyticsEvent.Name.RECOMMENDATION_IMPRESSION,
    AnalyticsEvent.Name.RECOMMENDATION_CLICK,
)


@shared_task
def prune_rec_events_task() -> dict:
    """Ištrina senus recommendation impression/click event'us.

    Impression'ai yra didžiausio volume'o event'ai — retention laiko juos
    ~90 d., ilgesnė analizė agreguojama per ataskaitas, ne žalius įrašus.
    """
    days = int(getattr(settings, "REC_EVENTS_RETENTION_DAYS", 90))
    cutoff = timezone.now() - timezone.timedelta(days=days)
    deleted, _ = AnalyticsEvent.objects.filter(
        name__in=REC_EVENT_NAMES, occurred_at__lt=cutoff
    ).delete()
    return {"deleted": int(deleted or 0), "retention_days": days}
