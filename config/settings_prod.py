from __future__ import annotations

from .settings_base import *  # noqa: F403

DEBUG = env.bool("DEBUG", default=False)  # type: ignore[name-defined]  # noqa: F405

# Security hardening for production (tunable via env)
SECURE_SSL_REDIRECT = env.bool("SECURE_SSL_REDIRECT", default=True)  # type: ignore[name-defined]  # noqa: F405
SESSION_COOKIE_SECURE = env.bool("SESSION_COOKIE_SECURE", default=True)  # type: ignore[name-defined]  # noqa: F405
CSRF_COOKIE_SECURE = env.bool("CSRF_COOKIE_SECURE", default=True)  # type: ignore[name-defined]  # noqa: F405
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
# SSR storefronts call the API over the internal network; the shop domain arrives as X-Forwarded-Host.
USE_X_FORWARDED_HOST = env.bool("USE_X_FORWARDED_HOST", default=False)  # type: ignore[name-defined]  # noqa: F405
# Container healthchecks call the health endpoint over plain HTTP.
SECURE_REDIRECT_EXEMPT = [rf"^{API_BASE_PATH}/v1/health$"]  # noqa: F405

STORAGES = {
    **STORAGES,  # noqa: F405
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"},
}
