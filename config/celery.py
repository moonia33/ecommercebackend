import logging
import os

from celery import Celery

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

app = Celery("django_ecommerce")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()

logger = logging.getLogger(__name__)


def safe_delay(task, *args, **kwargs):
    """Enqueue a Celery task without raising if the broker is unavailable."""
    try:
        return task.delay(*args, **kwargs)
    except Exception:
        logger.exception(
            "Failed to enqueue celery task %s", getattr(task, "name", task)
        )
        return None
