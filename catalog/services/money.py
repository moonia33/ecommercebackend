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


def product_offer_nets(variant_rows) -> tuple[Decimal, Decimal] | None:
    """Prekės kaina „nuo–iki“ kaip PDP: `variant_rows` – [(list_net, kanalo pasiūlymai)].

    Varianto kaina – pigiausias pasiūlymas su likučiu, jei likučio nėra – pigiausias iš visų
    sandėlių, be pasiūlymų – list kaina. „Nuo“ – iš variantų su likučiu (jei tokių yra),
    „iki“ – iš visų variantų."""
    in_stock: list[Decimal] = []
    nets: list[Decimal] = []
    for list_net, items in variant_rows:
        items = list(items)
        live = [ii for ii in items if ii.qty_available > 0]
        net = min(
            (effective_offer_unit_net(list_unit_net=list_net, offer=ii) for ii in (live or items)),
            default=Decimal(list_net),
        )
        nets.append(net)
        if live:
            in_stock.append(net)
    if not nets:
        return None
    return min(in_stock or nets), max(nets)


def listing_price_nets(products, *, channel: str) -> dict[int, tuple[Decimal, Decimal]]:
    """Sąrašams: {product_id: (nuo, iki)} pagal `product_offer_nets`, 2 užklausos puslapiui."""
    from catalog.models import InventoryItem, Variant

    ids = [int(p.id) for p in products]
    if not ids:
        return {}
    visibility = (
        InventoryItem.OfferVisibility.OUTLET if channel == "outlet" else InventoryItem.OfferVisibility.NORMAL
    )
    variants: dict[int, tuple[int, Decimal, list]] = {
        int(vid): (int(pid), Decimal(price), [])
        for vid, pid, price in Variant.objects.filter(
            product_id__in=ids, is_active=True, price_eur__isnull=False
        ).values_list("id", "product_id", "price_eur")
    }
    rows = InventoryItem.objects.filter(variant_id__in=variants, offer_visibility=visibility).only(
        "variant_id", "qty_on_hand", "qty_reserved", "never_discount",
        "offer_price_override_eur", "offer_discount_percent",
    )
    for ii in rows:
        variants[int(ii.variant_id)][2].append(ii)
    by_product: dict[int, list] = {}
    for pid, price, items in variants.values():
        by_product.setdefault(pid, []).append((price, items))
    out: dict[int, tuple[Decimal, Decimal]] = {}
    for pid, vrows in by_product.items():
        nets = product_offer_nets(vrows)
        if nets is not None:
            out[pid] = nets
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
