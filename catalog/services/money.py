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
    from catalog.models import InventoryItem, Variant

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
    # Variant'as be pasiūlymo šiame kanale – PDP rodo jo list kainą, todėl ir sąraše ji skaičiuojama.
    variant_nets: dict[int, tuple[int, Decimal]] = {
        int(vid): (int(pid), Decimal(price))
        for vid, pid, price in Variant.objects.filter(product_id__in=ids, is_active=True).values_list(
            "id", "product_id", "price_eur"
        )
    }
    offer_nets: dict[int, Decimal] = {}
    rows = (
        InventoryItem.objects.filter(variant_id__in=variant_nets, offer_visibility=visibility)
        .only("variant_id", "never_discount", "offer_price_override_eur", "offer_discount_percent")
    )
    for ii in rows:
        vid = int(ii.variant_id)
        net = effective_offer_unit_net(list_unit_net=variant_nets[vid][1], offer=ii)
        if vid not in offer_nets or net < offer_nets[vid]:
            offer_nets[vid] = net
    out: dict[int, Decimal] = {}
    for vid, (pid, price) in variant_nets.items():
        net = offer_nets.get(vid, price)
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
