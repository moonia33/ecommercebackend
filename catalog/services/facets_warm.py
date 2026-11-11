from __future__ import annotations

import logging
import time

logger = logging.getLogger(__name__)

WARM_SCHEDULED_KEY = "catalog:facets:warm:scheduled"
WARM_DEBOUNCE_SECONDS = 300


def warm_catalog_facets() -> dict:
    """Užpildo facets cache kiekvienos aktyvios kategorijos puslapiui be
    filtrų (pirmas kategorijos atidarymas nebelaukia ~1–2 s skaičiavimo)."""
    from ninja.errors import HttpError

    from api.models import Shop
    from catalog.api.products import cached_product_facets
    from catalog.models import Category

    started = time.monotonic()
    slugs = list(
        Category.objects.filter(is_active=True)
        .exclude(slug="")
        .values_list("slug", flat=True)
    )
    warmed = failed = 0
    for shop in Shop.objects.filter(is_active=True).order_by("id"):
        for slug in slugs:
            try:
                cached_product_facets(
                    shop_id=int(shop.id),
                    language_code=shop.default_language_code or "lt",
                    country_code=shop.default_country_code or "LT",
                    category_slug=slug,
                )
                warmed += 1
            except HttpError:
                failed += 1
            except Exception:
                failed += 1
                logger.exception("facets warm failed: shop=%s slug=%s", shop.id, slug)
    result = {
        "warmed": warmed,
        "failed": failed,
        "seconds": round(time.monotonic() - started, 1),
    }
    logger.info("facets warm: %s", result)
    return result


def schedule_facets_warm() -> None:
    """Debounce: po katalogo pakeitimų (importo metu – tūkstančiai signalų)
    vienas warm per WARM_DEBOUNCE_SECONDS."""
    from django.db import transaction

    transaction.on_commit(_enqueue_warm)


def _enqueue_warm() -> None:
    from django.core.cache import cache

    from catalog.tasks import warm_catalog_facets_task

    # Lock tik po commit'o: rollback'as neužblokuoja kito warm.
    if not cache.add(WARM_SCHEDULED_KEY, 1, WARM_DEBOUNCE_SECONDS):
        return
    try:
        warm_catalog_facets_task.apply_async(countdown=WARM_DEBOUNCE_SECONDS)
    except Exception:
        # Lock paliekamas: brokerio gedimo metu nekartojam bandymų kiekvienam
        # commit'ui; cache užpildys periodinis task'as.
        logger.exception("facets warm enqueue failed")
