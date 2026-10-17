import logging
import os

from celery import Celery

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

app = Celery("django_ecommerce")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()

logger = logging.getLogger(__name__)


_broker_down = False


def safe_delay(task, *args, **kwargs):
    """Enqueue a Celery task without raising if the broker is unavailable.

    Circuit breaker: po pirmo publish fail'o brokeris žymimas nesklandžiu
    ir sekančios užklausos grįžta iš karto (kombu retry_over_time kitaip
    blokuoja dešimtis sekundžių per kiekvieną kvietimą).
    """
    global _broker_down
    if _broker_down:
        return None
    try:
        return task.delay(*args, **kwargs)
    except Exception:
        _broker_down = True
        logger.exception(
            "Failed to enqueue celery task %s; broker marked down",
            getattr(task, "name", task),
        )
        return None
