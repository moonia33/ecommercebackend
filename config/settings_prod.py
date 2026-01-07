from __future__ import annotations

from .settings_base import *  # noqa: F403

DEBUG = env.bool("DEBUG", default=False)  # type: ignore[name-defined]  # noqa: F405

# Security hardening for production (tunable via env)
SECURE_SSL_REDIRECT = env.bool("SECURE_SSL_REDIRECT", default=True)  # type: ignore[name-defined]  # noqa: F405
SESSION_COOKIE_SECURE = env.bool("SESSION_COOKIE_SECURE", default=True)  # type: ignore[name-defined]  # noqa: F405
CSRF_COOKIE_SECURE = env.bool("CSRF_COOKIE_SECURE", default=True)  # type: ignore[name-defined]  # noqa: F405
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
