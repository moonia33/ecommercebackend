"""Sentry klaidų stebėjimas (web, Celery worker'iai, beat).

Įjungiama tik kai nustatytas SENTRY_DSN. Asmens duomenys (IP, cookies,
headeriai, request body) ir stack frame kintamieji nesiunčiami.
"""

from __future__ import annotations

import os


def _traces_sampler(ctx: dict) -> float:
    path = (ctx.get("wsgi_environ") or {}).get("PATH_INFO", "")
    if path.rstrip("/").endswith("/v1/health"):
        return 0.0
    return float(os.environ.get("SENTRY_TRACES_SAMPLE_RATE", "0.05"))


def _strip_headers(event: dict, _hint: dict) -> dict:
    # CF-Connecting-IP ir kt. proxy headeriai turi lankytojo IP.
    (event.get("request") or {}).pop("headers", None)
    return event


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
        release=os.environ.get("SENTRY_RELEASE")
        or os.environ.get("SOURCE_COMMIT")
        or None,
        send_default_pii=False,
        max_request_body_size="never",
        include_local_variables=False,
        traces_sampler=_traces_sampler,
        before_send=_strip_headers,
        before_send_transaction=_strip_headers,
        integrations=[DjangoIntegration(), CeleryIntegration()],
    )
    # Botai su svetimu Host – triukšmas, ne klaida.
    ignore_logger("django.security.DisallowedHost")
