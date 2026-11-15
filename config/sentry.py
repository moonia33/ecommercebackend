"""Sentry klaidų stebėjimas (web, Celery worker'iai, beat).

Įjungiama tik kai nustatytas SENTRY_DSN. Asmens duomenys (IP, cookies,
headeriai, request body) nesiunčiami.
"""

from __future__ import annotations

import os

HEALTH_PATHS = ("/api/v1/health",)


def _traces_sampler(ctx: dict) -> float:
    path = (ctx.get("wsgi_environ") or {}).get("PATH_INFO", "")
    if path in HEALTH_PATHS:
        return 0.0
    return float(os.environ.get("SENTRY_TRACES_SAMPLE_RATE", "0.05"))


def init_sentry() -> None:
    dsn = os.environ.get("SENTRY_DSN", "").strip()
    if not dsn:
        return

    import sentry_sdk
    from sentry_sdk.integrations.celery import CeleryIntegration
    from sentry_sdk.integrations.django import DjangoIntegration
    from sentry_sdk.integrations.logging import ignore_logger

    sentry_sdk.init(
        dsn=dsn,
        environment=os.environ.get("SENTRY_ENVIRONMENT", "production"),
        release=os.environ.get("SENTRY_RELEASE") or os.environ.get("SOURCE_COMMIT") or None,
        send_default_pii=False,
        max_request_body_size="never",
        traces_sampler=_traces_sampler,
        integrations=[DjangoIntegration(), CeleryIntegration()],
    )
    # Botai su svetimu Host – triukšmas, ne klaida.
    ignore_logger("django.security.DisallowedHost")
