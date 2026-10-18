from django.apps import AppConfig


class SearchConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "search"
    verbose_name = "Search (Typesense)"

    def ready(self):
        from . import signals  # noqa: F401
