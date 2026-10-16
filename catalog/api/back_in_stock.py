from __future__ import annotations

from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from ninja.errors import HttpError

from .shared import get_request_language_code, router
from ..api_schemas import BackInStockSubscribeIn, BackInStockSubscribeOut
from ..models import BackInStockSubscription, Product, Variant


@router.post("/back-in-stock/subscribe", response=BackInStockSubscribeOut)
def back_in_stock_subscribe(request, payload: BackInStockSubscribeIn):
    email = (payload.email or "").strip().lower()
    try:
        validate_email(email)
    except ValidationError:
        raise HttpError(400, "Invalid email")

    channel = (getattr(payload, "channel", None) or "normal").strip().lower()
    if channel not in {"normal", "outlet"}:
        raise HttpError(400, "Invalid channel")

    product = None
    variant = None

    if payload.variant_id:
        variant = (
            Variant.objects.filter(id=int(payload.variant_id))
            .select_related("product")
            .first()
        )
        if not variant:
            raise HttpError(404, "Variant not found")
        product = variant.product

    if payload.product_id:
        product = Product.objects.filter(id=int(payload.product_id)).first()
        if not product:
            raise HttpError(404, "Product not found")

    if not product and not variant:
        raise HttpError(400, "product_id or variant_id is required")

    language_code = get_request_language_code(request)

    obj, created = BackInStockSubscription.objects.get_or_create(
        email=email,
        product=product,
        variant=variant,
        channel=channel,
        defaults={"is_active": True, "language_code": language_code},
    )
    if not created and not obj.is_active:
        obj.is_active = True
        obj.save(update_fields=["is_active"])

    if not created and (obj.language_code or "").strip().lower() != (language_code or "").strip().lower():
        obj.language_code = language_code
        obj.save(update_fields=["language_code"])

    return {"status": "ok"}
