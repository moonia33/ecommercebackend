from __future__ import annotations

from django.apps import AppConfig


class ShippingConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "shipping"

    def ready(self) -> None:
        from . import services  # noqa: F401  (registers Holiday cache invalidation signals)
