from __future__ import annotations

from django.conf import settings
from ninja import NinjaAPI

from accounts.api import router as auth_router
from checkout.api import router as checkout_router
from catalog.api import router as catalog_router
from pricing.api import router as pricing_router
from dpd.api import router as dpd_router
from unisend.api import router as unisend_router
from payments.api import router as payments_router
from cms.api import router as cms_router
from homebuilder.api import router as home_router

docs_url = "/docs" if getattr(settings, "NINJA_ENABLE_DOCS", True) else None
openapi_url = "/openapi.json" if getattr(settings,
                                         "NINJA_ENABLE_DOCS", True) else None

api = NinjaAPI(
    title="Djengo e-commerce API",
    version="1",
    docs_url=docs_url,
    openapi_url=openapi_url,
)

api.add_router("/auth", auth_router)
api.add_router("/catalog", catalog_router)
api.add_router("/pricing", pricing_router)
api.add_router("/checkout", checkout_router)
api.add_router("/payments", payments_router)
api.add_router("/dpd", dpd_router)
api.add_router("/unisend", unisend_router)
api.add_router("/cms", cms_router)
api.add_router("", home_router)


@api.get("/health")
def health(request):
    return {"status": "ok"}
