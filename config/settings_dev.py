from __future__ import annotations

from .settings_base import *  # noqa: F403

# Development defaults
DEBUG = env.bool("DEBUG", default=True)  # type: ignore[name-defined]  # noqa: F405
