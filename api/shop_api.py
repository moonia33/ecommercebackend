from __future__ import annotations

from django.core.cache import cache
from ninja import Router, Schema

from .models import ShopConfig
from .shop_context import get_request_shop

router = Router(tags=["shop"])

CACHE_TTL = 300


class ShopConfigOut(Schema):
    code: str = ""
    name: str = ""
    site_name: str = ""
    logo_url: str | None = None
    default_title: str = ""
    default_description: str = ""
    terms_url: str = ""
    privacy_url: str = ""
    enable_checkout: bool = True
    enable_catalog: bool = True


def shop_config_cache_key(shop_id: int | None) -> str:
    return f"shop:config:{shop_id if shop_id is not None else 'null'}"


@router.get("/config", response=ShopConfigOut)
def shop_config(request):
    shop = get_request_shop(request)
    key = shop_config_cache_key(shop.id if shop else None)
    cached = cache.get(key)
    if cached is not None:
        return cached

    out: dict = {}
    if shop is not None:
        out = {"code": shop.code, "name": shop.name}
        cfg = ShopConfig.objects.filter(shop=shop).first()
        if cfg is not None:
            out.update(
                site_name=cfg.site_name,
                logo_url=(cfg.logo.url if cfg.logo else None),
                default_title=cfg.default_title,
                default_description=cfg.default_description,
                terms_url=cfg.terms_url,
                privacy_url=cfg.privacy_url,
                enable_checkout=cfg.enable_checkout,
                enable_catalog=cfg.enable_catalog,
            )
    data = ShopConfigOut(**out).dict()
    cache.set(key, data, CACHE_TTL)
    return data
