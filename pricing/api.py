from __future__ import annotations

from decimal import Decimal

from ninja import Router
from ninja.errors import HttpError

from catalog.models import Variant

from .schemas import QuoteOut
from .services import compute_vat, get_vat_rate

router = Router(tags=["pricing"])


@router.get("/quote", response=QuoteOut)
def quote(request, variant_id: int, country_code: str, qty: int = 1):
    variant = (
        Variant.objects.select_related("product", "product__tax_class")
        .filter(id=variant_id, is_active=True)
        .first()
    )
    if not variant:
        raise HttpError(404, "Variant not found")

    product = variant.product
    if not product or not product.tax_class_id:
        raise HttpError(400, "Product has no tax_class assigned")

    country_code = (country_code or "").strip().upper()
    if len(country_code) != 2:
        raise HttpError(400, "Invalid country_code")

    qty = int(qty or 1)
    if qty <= 0:
        raise HttpError(400, "qty must be positive")

    unit_net = Decimal(variant.price_eur)

    try:
        vat_rate = get_vat_rate(country_code=country_code,
                                tax_class=product.tax_class)
    except LookupError:
        raise HttpError(400, "VAT rate not configured for country/tax_class")
    except ValueError as exc:
        raise HttpError(400, str(exc))

    breakdown = compute_vat(unit_net=unit_net, vat_rate=vat_rate, qty=qty)

    return {
        "variant_id": variant.id,
        "country_code": country_code,
        "unit_net": breakdown.unit_net,
        "vat_rate": breakdown.vat_rate,
        "unit_vat": breakdown.unit_vat,
        "unit_gross": breakdown.unit_gross,
        "qty": qty,
        "total_net": breakdown.total_net,
        "total_vat": breakdown.total_vat,
        "total_gross": breakdown.total_gross,
    }
