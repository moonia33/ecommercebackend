from django.apps import AppConfig


class ApiConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "api"

    def ready(self):
        from django.core.cache import cache
        from django.db.models.signals import post_delete, post_save

        from .models import Shop, ShopConfig
        from .shop_api import shop_config_cache_key

        def _drop(sender, instance, **kwargs):
            shop_id = instance.id if sender is Shop else instance.shop_id
            cache.delete(shop_config_cache_key(shop_id))

        for model in (Shop, ShopConfig):
            post_save.connect(_drop, sender=model, dispatch_uid=f"shop_config_cache_{model.__name__}")
            post_delete.connect(_drop, sender=model, dispatch_uid=f"shop_config_cache_del_{model.__name__}")
