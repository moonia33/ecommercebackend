from __future__ import annotations

from decimal import Decimal

from pricing.services import compute_vat


def money_out(*, currency: str, unit_net: Decimal, vat_rate: Decimal) -> dict:
    b = compute_vat(unit_net=Decimal(unit_net), vat_rate=Decimal(vat_rate), qty=1)
    return {
        "currency": currency,
        "net": b.unit_net,
        "vat_rate": b.vat_rate,
        "vat": b.unit_vat,
        "gross": b.unit_gross,
    }


def effective_offer_unit_net(*, list_unit_net: Decimal, offer) -> Decimal:
    if bool(getattr(offer, "never_discount", False)):
        return Decimal(list_unit_net)
    if getattr(offer, "offer_price_override_eur", None) is not None:
        return Decimal(offer.offer_price_override_eur)
    if getattr(offer, "offer_discount_percent", None) is not None:
        pct = int(offer.offer_discount_percent)
        pct = max(0, min(100, pct))
        return (Decimal(list_unit_net) * (Decimal(100 - pct) / Decimal(100))).quantize(Decimal("0.01"))
    return Decimal(list_unit_net)


def discount_percent(*, list_unit_net: Decimal, sale_unit_net: Decimal) -> int | None:
    list_unit_net = Decimal(list_unit_net or 0)
    sale_unit_net = Decimal(sale_unit_net or 0)
    if list_unit_net <= 0:
        return None
    if sale_unit_net >= list_unit_net:
        return None
    pct = int(((list_unit_net - sale_unit_net) / list_unit_net) * Decimal(100))
    return max(0, min(100, pct))
