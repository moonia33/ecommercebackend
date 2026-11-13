from django.apps import AppConfig


class RecommendationsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "recommendations"
    verbose_name = "Recommendations"

    def ready(self):
        from django.db.models.signals import post_delete, post_save

        from .models import SeasonalBoost
        from .services import invalidate_boost_cache

        post_save.connect(
            invalidate_boost_cache, sender=SeasonalBoost,
            dispatch_uid="recs_boost_save")
        post_delete.connect(
            invalidate_boost_cache, sender=SeasonalBoost,
            dispatch_uid="recs_boost_del")
