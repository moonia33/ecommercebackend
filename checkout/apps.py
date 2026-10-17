from __future__ import annotations

from django.apps import AppConfig


class CheckoutConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "checkout"

    def ready(self) -> None:  # noqa: D401
        import checkout.signals  # noqa: F401
