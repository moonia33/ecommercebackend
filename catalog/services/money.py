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


def price_offer(offers, *, list_unit_net: Decimal):
    """Pasiūlymas kainai: geriausias su likučiu; jei likučio nėra nė viename sandėlyje –
    geriausias iš visų, kad išparduotos prekės nuolaida (pvz. tiekėjo SALE) neišnyktų."""
    offers = list(offers)
    pool = [ii for ii in offers if ii.qty_available > 0] or offers
    if not pool:
        return None
    return min(
        pool,
        key=lambda ii: (
            -int(ii.offer_priority or 0),
            effective_offer_unit_net(list_unit_net=Decimal(list_unit_net), offer=ii),
            int(ii.id),
        ),
    )


def out_of_stock_offer_nets(products, *, channel: str) -> dict[int, Decimal]:
    """Sąrašams: prekėms be likučio (`_min_offer_price` None) – pigiausia pasiūlymo kaina
    iš visų sandėlių (be likučio), kaip `offer_price_exprs`, tik be likučio filtro."""
    from catalog.models import InventoryItem

    ids = [
        int(p.id)
        for p in products
        if getattr(p, "_min_offer_price", None) is None and getattr(p, "_min_variant_price", None) is not None
    ]
    if not ids:
        return {}
    visibility = (
        InventoryItem.OfferVisibility.OUTLET if channel == "outlet" else InventoryItem.OfferVisibility.NORMAL
    )
    out: dict[int, Decimal] = {}
    rows = (
        InventoryItem.objects.filter(
            variant__product_id__in=ids, variant__is_active=True, offer_visibility=visibility
        )
        .select_related("variant")
        .only(
            "never_discount", "offer_price_override_eur", "offer_discount_percent",
            "variant__product_id", "variant__price_eur",
        )
    )
    for ii in rows:
        net = effective_offer_unit_net(list_unit_net=Decimal(ii.variant.price_eur), offer=ii)
        pid = int(ii.variant.product_id)
        if pid not in out or net < out[pid]:
            out[pid] = net
    return out


def discount_percent(*, list_unit_net: Decimal, sale_unit_net: Decimal) -> int | None:
    list_unit_net = Decimal(list_unit_net or 0)
    sale_unit_net = Decimal(sale_unit_net or 0)
    if list_unit_net <= 0:
        return None
    if sale_unit_net >= list_unit_net:
        return None
    pct = int(((list_unit_net - sale_unit_net) / list_unit_net) * Decimal(100))
    return max(0, min(100, pct))
