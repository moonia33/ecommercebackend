from __future__ import annotations

from .settings_base import *  # noqa: F403

# Development defaults
DEBUG = env.bool("DEBUG", default=True)  # type: ignore[name-defined]  # noqa: F405

# Use local memory cache instead of Redis in development
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
    }
}

# Run Celery tasks eagerly in development/tests so no broker is needed.
CELERY_TASK_ALWAYS_EAGER = env.bool("CELERY_TASK_ALWAYS_EAGER", default=True)  # type: ignore[name-defined]  # noqa: F405
