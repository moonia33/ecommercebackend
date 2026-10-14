from __future__ import annotations

from .settings_base import *  # noqa: F403

# Development defaults
DEBUG = env.bool("DEBUG", default=True)  # type: ignore[name-defined]  # noqa: F405

# Local dev runs without Redis unless USE_REDIS=true.
if not env.bool("USE_REDIS", default=False):  # type: ignore[name-defined]  # noqa: F405
    CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}
