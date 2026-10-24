from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.db import models
from django.db import transaction
from django.utils import timezone

from catalog.models import TaxClass
from pricing.services import compute_vat, get_vat_rate


@dataclass(frozen=True)
class Money:
    currency: str
    net: Decimal
    vat_rate: Decimal
    vat: Decimal
    gross: Decimal


def money_from_net(*, currency: str, unit_net: Decimal, vat_rate: Decimal, qty: int = 1) -> Money:
    breakdown = compute_vat(unit_net=Decimal(unit_net),
                            vat_rate=Decimal(vat_rate), qty=int(qty))
    return Money(
        currency=currency,
        net=breakdown.total_net,
        vat_rate=breakdown.vat_rate,
        vat=breakdown.total_vat,
        gross=breakdown.total_gross,
    )


def get_shipping_net(*, shipping_method: str, country_code: str) -> Decimal:
    from shipping.models import ShippingRate

    shipping_method = (shipping_method or "").strip()
    country_code = (country_code or "").strip().upper()

    if not shipping_method:
        raise ValueError("shipping_method is required")
    if len(country_code) != 2:
        raise ValueError("Invalid country_code")

    rate = (
        ShippingRate.objects.select_related("method")
        .filter(
            method__code=shipping_method,
            method__is_active=True,
            is_active=True,
            country_code=country_code,
        )
        .first()
    )
    if rate:
        return Decimal(rate.net_eur)

    # Backward-compatible fallback for older setups.
    if shipping_method in {"lpexpress", "unisend_pickup"}:
        return Decimal(str(getattr(settings, "LPEXPRESS_SHIPPING_NET_EUR", "0.00")))

    raise ValueError("Unsupported shipping_method")


def get_shipping_tax_class() -> TaxClass | None:
    # MVP: use standard tax class for shipping VAT.
    code = getattr(settings, "DEFAULT_SHIPPING_TAX_CLASS_CODE", "standard")
    from catalog.models import TaxClass

    return TaxClass.objects.filter(code=code).first()


def get_vat_rate_for(*, country_code: str, tax_class: TaxClass) -> Decimal:
    return get_vat_rate(country_code=country_code, tax_class=tax_class)


def calculate_fee_money(
    *,
    currency: str,
    country_code: str,
    amount_net: Decimal,
    tax_class: TaxClass | None,
) -> Money:
    if not tax_class or not amount_net:
        return Money(
            currency=currency,
            net=Decimal(amount_net or 0),
            vat_rate=Decimal("0"),
            vat=Decimal("0"),
            gross=Decimal(amount_net or 0),
        )
    vat_rate = get_vat_rate(country_code=country_code, tax_class=tax_class)
    return money_from_net(currency=currency, unit_net=amount_net, vat_rate=vat_rate, qty=1)


def select_fee_rules(*, country_code: str, payment_method: str) -> list["FeeRule"]:
    from checkout.models import FeeRule

    country_code = (country_code or "").strip().upper()
    payment_method = (payment_method or "").strip()

    qs = FeeRule.objects.filter(is_active=True).order_by("sort_order", "code")
    if country_code:
        qs = qs.filter(models.Q(country_code="") | models.Q(country_code=country_code))
    if payment_method:
        qs = qs.filter(
            models.Q(payment_method_code="") | models.Q(payment_method_code=payment_method)
        )
    return list(qs)


def calculate_fees(
    *,
    currency: str,
    country_code: str,
    items_gross: Decimal,
    payment_method: str,
) -> list[tuple["FeeRule", Money]]:
    rules = select_fee_rules(country_code=country_code, payment_method=payment_method)
    out: list[tuple["FeeRule", Money]] = []
    items_gross_d = Decimal(items_gross or 0)

    for r in rules:
        if r.min_items_gross is not None and items_gross_d < Decimal(r.min_items_gross):
            continue
        if r.max_items_gross is not None and items_gross_d > Decimal(r.max_items_gross):
            continue

        m = calculate_fee_money(
            currency=currency,
            country_code=country_code,
            amount_net=Decimal(r.amount_net),
            tax_class=r.tax_class,
        )
        out.append((r, m))

    return out


def inventory_available_for_variant(*, variant_id: int) -> int:
    from catalog.models import InventoryItem

    agg = InventoryItem.objects.filter(variant_id=int(variant_id)).aggregate(
        total=models.Sum(models.F("qty_on_hand") - models.F("qty_reserved"))
    )
    total = agg.get("total")
    try:
        return max(0, int(total or 0))
    except Exception:
        return 0


def inventory_available_for_offer(*, offer_id: int) -> int:
    from catalog.models import InventoryItem

    offer_id = int(offer_id)
    inv = InventoryItem.objects.filter(id=offer_id).first()
    if not inv:
        return 0
    return max(0, int(inv.qty_on_hand) - int(inv.qty_reserved))


def reserve_inventory_for_order(*, order_id: int) -> None:
    from catalog.models import InventoryItem
    from checkout.models import InventoryAllocation, OrderLine

    order_id = int(order_id)

    lines = list(
        OrderLine.objects.filter(order_id=order_id)
        .select_related("variant", "variant__product")
        .order_by("id")
    )
    # Skaitmeninės dovanų kortelės neturi inventoriaus — praleidžiamos.
    lines = [
        ln for ln in lines
        if not getattr(getattr(ln.variant, "product", None), "is_gift_card", False)
    ]
    if not lines:
        return

    inv_items = list(
        InventoryItem.objects.select_related("warehouse")
        .select_for_update()
        .filter(variant_id__in=[ln.variant_id for ln in lines if ln.variant_id])
        .order_by("warehouse__sort_order", "warehouse__code", "id")
    )
    inv_by_variant: dict[int, list[InventoryItem]] = {}
    for it in inv_items:
        inv_by_variant.setdefault(int(it.variant_id), []).append(it)

    allocations_to_create: list[InventoryAllocation] = []
    updates: list[InventoryItem] = []
    now = timezone.now()

    for ln in lines:
        if not ln.variant_id:
            continue
        need = int(ln.qty)
        if need <= 0:
            continue

        if ln.offer_id:
            inv = next((i for i in inv_items if int(i.id) == int(ln.offer_id)), None)
            if not inv:
                raise ValueError("No inventory for offer")
            available = max(0, int(inv.qty_on_hand) - int(inv.qty_reserved))
            if available < need:
                raise ValueError("Not enough stock")
            inv.qty_reserved = int(inv.qty_reserved) + int(need)
            inv.updated_at = now
            updates.append(inv)
            allocations_to_create.append(
                InventoryAllocation(
                    order_id=order_id,
                    order_line=ln,
                    inventory_item=inv,
                    qty=int(need),
                    status=InventoryAllocation.Status.RESERVED,
                )
            )
            continue

        candidates = inv_by_variant.get(int(ln.variant_id), [])
        if not candidates:
            raise ValueError("No inventory for variant")

        for inv in candidates:
            if need <= 0:
                break
            available = max(0, int(inv.qty_on_hand) - int(inv.qty_reserved))
            if available <= 0:
                continue
            take = min(available, need)
            if take <= 0:
                continue
            inv.qty_reserved = int(inv.qty_reserved) + int(take)
            inv.updated_at = now
            updates.append(inv)
            allocations_to_create.append(
                InventoryAllocation(
                    order_id=order_id,
                    order_line=ln,
                    inventory_item=inv,
                    qty=int(take),
                    status=InventoryAllocation.Status.RESERVED,
                )
            )
            need -= int(take)

        if need > 0:
            raise ValueError("Not enough stock")

    if updates:
        InventoryItem.objects.bulk_update(updates, ["qty_reserved", "updated_at"])
    if allocations_to_create:
        InventoryAllocation.objects.bulk_create(allocations_to_create)
        # Backfill line.offer from the first allocated item so admin/UI can
        # show the source warehouse for lines placed without an explicit offer.
        first_alloc_by_line: dict[int, int] = {}
        for a in allocations_to_create:
            first_alloc_by_line.setdefault(int(a.order_line_id), int(a.inventory_item_id))
        for ln in lines:
            inv_id = first_alloc_by_line.get(int(ln.id))
            if not ln.offer_id and inv_id:
                ln.offer_id = inv_id
                ln.save(update_fields=["offer"])


def capture_inventory_for_order(*, order_id: int) -> None:
    from catalog.models import InventoryItem
    from checkout.models import InventoryAllocation

    order_id = int(order_id)
    rows = list(
        InventoryAllocation.objects.select_for_update()
        .filter(order_id=order_id, status=InventoryAllocation.Status.RESERVED)
        .select_related("inventory_item")
        .order_by("id")
    )
    if not rows:
        return

    inv_ids = [r.inventory_item_id for r in rows]
    inv_items = {
        i.id: i
        for i in InventoryItem.objects.select_for_update()
        .filter(id__in=inv_ids)
        .order_by("id")
    }

    inv_updates: list[InventoryItem] = []
    alloc_updates: list[InventoryAllocation] = []
    now = timezone.now()

    for r in rows:
        inv = inv_items.get(r.inventory_item_id)
        if not inv:
            continue
        q = int(r.qty)
        inv.qty_on_hand = max(0, int(inv.qty_on_hand) - q)
        inv.qty_reserved = max(0, int(inv.qty_reserved) - q)
        inv.updated_at = now
        inv_updates.append(inv)
        r.status = InventoryAllocation.Status.CAPTURED
        r.updated_at = now
        alloc_updates.append(r)

    if inv_updates:
        InventoryItem.objects.bulk_update(inv_updates, ["qty_on_hand", "qty_reserved", "updated_at"])
    if alloc_updates:
        InventoryAllocation.objects.bulk_update(alloc_updates, ["status", "updated_at"])


def release_inventory_for_order(*, order_id: int) -> None:
    from catalog.models import InventoryItem
    from checkout.models import InventoryAllocation

    order_id = int(order_id)
    rows = list(
        InventoryAllocation.objects.select_for_update()
        .filter(order_id=order_id, status=InventoryAllocation.Status.RESERVED)
        .select_related("inventory_item")
        .order_by("id")
    )
    if not rows:
        return

    now = timezone.now()

    inv_ids = [r.inventory_item_id for r in rows]
    inv_items = {
        i.id: i
        for i in InventoryItem.objects.select_for_update()
        .filter(id__in=inv_ids)
        .order_by("id")
    }

    inv_updates: list[InventoryItem] = []
    alloc_updates: list[InventoryAllocation] = []

    for r in rows:
        inv = inv_items.get(r.inventory_item_id)
        if not inv:
            continue
        q = int(r.qty)
        inv.qty_reserved = max(0, int(inv.qty_reserved) - q)
        inv.updated_at = now
        inv_updates.append(inv)
        r.status = InventoryAllocation.Status.RELEASED
        r.updated_at = now
        alloc_updates.append(r)

    if inv_updates:
        InventoryItem.objects.bulk_update(inv_updates, ["qty_reserved", "updated_at"])
    if alloc_updates:
        InventoryAllocation.objects.bulk_update(alloc_updates, ["status", "updated_at"])


def finalize_paid_order(*, order_id: int) -> None:
    """Shared side-effects every time an order reaches PAID.

    Called from all payment-success paths (Montonio webhook, Inbank
    callback, gift-card full cover, ops mark-paid): captures reserved
    stock, redeems the coupon usage, issues purchased gift cards, and for
    digital-only orders anchors DELIVERED so the billing signal issues
    the invoice right away.
    """
    from checkout.models import Order

    capture_inventory_for_order(order_id=order_id)

    from promotions.services import redeem_coupon_for_paid_order
    redeem_coupon_for_paid_order(order_id=order_id)

    try:
        from giftcards.services import issue_cards_for_paid_order
        order = Order.objects.select_related("user").get(pk=order_id)
        issue_cards_for_paid_order(order=order)
    except Exception:
        import logging
        logging.getLogger(__name__).exception(
            "gift card issuance failed for order %s", order_id)

    # Visas krepšelis skaitmeninis → nieko nepristatoma; DELIVERED
    # užkerta delivered_at + trigger'ina sąskaitą per billing signal'ą.
    order = Order.objects.get(pk=order_id)
    has_physical = order.lines.filter(
        variant__product__is_gift_card=False).exists()
    if not has_physical and order.delivery_status != Order.DeliveryStatus.DELIVERED:
        order.delivery_status = Order.DeliveryStatus.DELIVERED
        order.save(update_fields=["delivery_status", "updated_at"])


def reserve_inventory_for_line(*, order_line, qty: int | None = None,
                               strict: bool = True) -> int:
    """Reserve inventory for a single OrderLine (admin line-add / qty increase).
    Same warehouse ordering as reserve_inventory_for_order; when the line pins
    an offer, only that item is used. Returns the allocated qty. With
    strict=True a shortfall raises ValueError."""
    from catalog.models import InventoryItem
    from checkout.models import InventoryAllocation

    line = order_line
    need = int(qty if qty is not None else line.qty)
    if need <= 0 or not line.variant_id:
        return 0
    if getattr(getattr(line.variant, "product", None), "is_gift_card", False):
        return 0

    items = list(
        InventoryItem.objects.select_related("warehouse")
        .select_for_update()
        .filter(variant_id=int(line.variant_id))
        .order_by("warehouse__sort_order", "warehouse__code", "id")
    )
    if line.offer_id:
        items = [i for i in items if int(i.id) == int(line.offer_id)]
    if not items:
        if strict:
            raise ValueError("No inventory for variant")
        return 0

    allocated = 0
    first_inv = None
    updates: list[InventoryItem] = []
    now = timezone.now()
    for inv in items:
        if need <= 0:
            break
        available = max(0, int(inv.qty_on_hand) - int(inv.qty_reserved))
        take = min(available, need)
        if take <= 0:
            continue
        inv.qty_reserved = int(inv.qty_reserved) + int(take)
        inv.updated_at = now
        updates.append(inv)
        InventoryAllocation.objects.create(
            order_id=int(line.order_id),
            order_line=line,
            inventory_item=inv,
            qty=int(take),
            status=InventoryAllocation.Status.RESERVED,
        )
        if first_inv is None:
            first_inv = inv
        allocated += int(take)
        need -= int(take)

    if updates:
        InventoryItem.objects.bulk_update(updates, ["qty_reserved", "updated_at"])
    if first_inv is not None and not line.offer_id:
        line.offer_id = int(first_inv.id)
        line.save(update_fields=["offer"])

    if strict and need > 0:
        raise ValueError("Not enough stock")
    return allocated


def release_inventory_for_line(*, order_line, qty: int | None = None) -> int:
    """Release allocations for one OrderLine (line removed / qty reduced).
    RESERVED rows give qty_reserved back; CAPTURED rows return qty_on_hand
    (goods already deducted stay in the warehouse). Returns released qty."""
    from catalog.models import InventoryItem
    from checkout.models import InventoryAllocation

    line = order_line
    remaining = qty if qty is not None else None

    rows = list(
        InventoryAllocation.objects.select_for_update()
        .filter(
            order_line_id=int(line.id),
            status__in=[
                InventoryAllocation.Status.RESERVED,
                InventoryAllocation.Status.CAPTURED,
            ],
        )
        .order_by("id")
    )
    # One Python instance per inventory row — several allocations may point to
    # the same item and a per-allocation copy would overwrite earlier deltas.
    inv_items = {
        i.id: i
        for i in InventoryItem.objects.select_for_update()
        .filter(id__in={r.inventory_item_id for r in rows})
        .order_by("id")
    }
    released = 0
    now = timezone.now()
    for r in rows:
        if remaining is not None and remaining <= 0:
            break
        take = int(r.qty) if remaining is None else min(int(r.qty), int(remaining))
        if take <= 0:
            continue
        inv = inv_items.get(r.inventory_item_id)
        if inv is None:
            continue
        if r.status == InventoryAllocation.Status.RESERVED:
            inv.qty_reserved = max(0, int(inv.qty_reserved) - take)
        else:
            inv.qty_on_hand = int(inv.qty_on_hand) + take
        inv.updated_at = now
        inv.save(update_fields=["qty_reserved", "qty_on_hand", "updated_at"])
        r.qty = int(r.qty) - take
        r.status = (
            InventoryAllocation.Status.RELEASED if int(r.qty) <= 0 else r.status
        )
        r.updated_at = now
        r.save(update_fields=["qty", "status", "updated_at"])
        released += take
        if remaining is not None:
            remaining -= take
    return released


def expire_pending_payment_reservations(*, now=None) -> int:
    from checkout.models import Order, PaymentIntent

    now = now or timezone.now()

    ttl_gateway_min = int(getattr(settings, "INVENTORY_RESERVATION_TTL_MINUTES_GATEWAY", 30) or 30)
    ttl_bank_hours = int(getattr(settings, "INVENTORY_RESERVATION_TTL_HOURS_BANK_TRANSFER", 72) or 72)
    ttl_inbank_hours = int(getattr(settings, "INVENTORY_RESERVATION_TTL_HOURS_INBANK", 24) or 24)

    cutoff_gateway = now - timedelta(minutes=ttl_gateway_min)
    cutoff_bank = now - timedelta(hours=ttl_bank_hours)
    cutoff_inbank = now - timedelta(hours=ttl_inbank_hours)

    qs = (
        Order.objects.filter(status=Order.Status.PENDING_PAYMENT)
        .select_related("payment_intent")
        .order_by("id")
    )

    expired_ids: list[int] = []
    for o in qs:
        pi = getattr(o, "payment_intent", None)
        provider = (getattr(pi, "provider", "") or "").strip()
        if provider == PaymentIntent.Provider.COD:
            continue  # pays at pickup/delivery — never expires
        if provider == PaymentIntent.Provider.BANK_TRANSFER:
            if o.created_at < cutoff_bank:
                expired_ids.append(o.id)
        elif provider == PaymentIntent.Provider.INBANK:
            if o.created_at < cutoff_inbank:
                expired_ids.append(o.id)
        else:
            if o.created_at < cutoff_gateway:
                expired_ids.append(o.id)

    if not expired_ids:
        return 0

    expired = 0
    with transaction.atomic():
        for oid in expired_ids:
            o = Order.objects.select_for_update(of=("self",)).select_related("payment_intent").filter(id=oid).first()
            if not o:
                continue
            if o.status != Order.Status.PENDING_PAYMENT:
                continue

            release_inventory_for_order(order_id=o.id)
            try:
                from giftcards.services import restore_card_debits_for_order
                restore_card_debits_for_order(order=o)
            except Exception:
                pass
            o.status = Order.Status.CANCELLED
            o.save(update_fields=["status", "updated_at"])

            pi = getattr(o, "payment_intent", None)
            if pi and pi.status not in {PaymentIntent.Status.SUCCEEDED, PaymentIntent.Status.CANCELLED}:
                pi.status = PaymentIntent.Status.CANCELLED
                pi.save(update_fields=["status", "updated_at"])

            expired += 1

    return expired


def order_shipping_snapshot(order) -> dict:
    """Effective receiver data for carrier integrations.

    Order-level shipping fields win; missing values fall back to the
    customer's saved shipping address (is_default_shipping first), then
    the primary phone, then the user record. Orders created in admin may
    have no shipping_* fields even though the customer profile does.
    """
    user = getattr(order, "user", None)
    addr = None
    if user is not None:
        try:
            addr = (
                user.addresses.filter(is_default_shipping=True).first()
                or user.addresses.first()
            )
        except Exception:
            addr = None
    phone_row = None
    if user is not None:
        try:
            phone_row = user.phones.first()  # ordered -is_primary first
        except Exception:
            phone_row = None

    def pick(order_val: str, *fallbacks) -> str:
        v = (order_val or "").strip()
        if v:
            return v
        for fb in fallbacks:
            v = (fb or "").strip()
            if v:
                return v
        return ""

    return {
        "full_name": pick(
            order.shipping_full_name,
            getattr(addr, "full_name", ""),
            getattr(user, "get_full_name", lambda: "")() if user else "",
        ) or "Customer",
        "company": pick(order.shipping_company, getattr(addr, "company", "")),
        "line1": pick(order.shipping_line1, getattr(addr, "line1", "")),
        "city": pick(order.shipping_city, getattr(addr, "city", "")),
        "postal_code": pick(
            order.shipping_postal_code, getattr(addr, "postal_code", "")),
        "country_code": pick(
            order.shipping_country_code,
            order.country_code,
            getattr(addr, "country_code", ""),
        ),
        "phone": pick(
            order.shipping_phone,
            getattr(addr, "phone", ""),
            getattr(phone_row, "phone", ""),
        ),
        "email": pick(getattr(user, "email", "") or ""),
    }


def preferred_pickup_point(order, *, shipping_method: str = "") -> dict | None:
    """Customer's saved pickup-point preference (UserPickupPoint), when it
    matches the shipping method. Used as a fallback when the order has no
    pickup point selected."""
    user = getattr(order, "user", None)
    if user is None:
        return None
    pref = getattr(user, "primary_pickup_point", None)
    if pref is None or not (pref.pickup_point_id or "").strip():
        return None
    method = (shipping_method or order.shipping_method or "").strip()
    if (pref.shipping_method_code or "").strip() and \
            pref.shipping_method_code != method:
        return None
    locker = None
    try:
        from dpd.models import DpdLocker
        locker = DpdLocker.objects.filter(
            locker_id=pref.pickup_point_id).first()
    except Exception:
        locker = None
    return {
        "pickup_point_id": (pref.pickup_point_id or "").strip(),
        "name": (pref.pickup_point_name or "").strip(),
        "country_code": (pref.country_code or "").strip(),
        "raw": dict(pref.pickup_point_raw or {}),
        "locker": locker,
    }


def recompute_order_fulfillment_mode(*, order) -> None:
    """Derive fulfillment_mode from the warehouses of the order's offers.

    local only -> stock; remote only -> remote; mix -> mixed.
    """
    from catalog.models import Warehouse
    from checkout.models import Order

    kinds = set(
        order.lines.exclude(offer_id=None)
        .exclude(offer__warehouse_id=None)
        .values_list("offer__warehouse__kind", flat=True)
    )
    if not kinds:
        mode = Order.FulfillmentMode.STOCK
    elif kinds == {Warehouse.Kind.REMOTE}:
        mode = Order.FulfillmentMode.REMOTE
    elif Warehouse.Kind.REMOTE in kinds:
        mode = Order.FulfillmentMode.MIXED
    else:
        mode = Order.FulfillmentMode.STOCK

    if order.fulfillment_mode != mode:
        order.fulfillment_mode = mode
        order.save(update_fields=["fulfillment_mode", "updated_at"])


def recompute_order_delivery_eta(*, order, now=None) -> None:
    """Aggregate per-line delivery windows into order ETA snapshot."""
    from catalog.models import InventoryItem
    from shipping.services import estimate_delivery_window

    now = now or timezone.now()
    agg_min = agg_max = agg_meta = None

    lines = order.lines.select_related(
        "variant", "variant__product", "offer").all()
    for ln in lines:
        try:
            v = getattr(ln, "variant", None)
            p = getattr(v, "product", None) if v is not None else None
            offer = getattr(ln, "offer", None)
            channel = (
                "outlet"
                if offer
                and offer.offer_visibility == InventoryItem.OfferVisibility.OUTLET
                else "normal"
            )
            dw = estimate_delivery_window(
                now=now,
                country_code=order.country_code,
                channel=channel,
                warehouse_id=int(offer.warehouse_id) if offer and offer.warehouse_id else None,
                product_id=int(p.id) if p else None,
                brand_id=int(p.brand_id) if p and p.brand_id else None,
                category_id=int(p.category_id) if p and p.category_id else None,
                product_group_id=int(getattr(p, "group_id", None))
                if p and getattr(p, "group_id", None) else None,
            )
        except Exception:
            dw = None
        if dw is None:
            continue
        if agg_min is None or dw.min_date > agg_min:
            agg_min = dw.min_date
        if agg_max is None or dw.max_date > agg_max:
            agg_max = dw.max_date
        agg_meta = dw

    order.delivery_min_date = agg_min
    order.delivery_max_date = agg_max
    order.delivery_eta_kind = str(getattr(agg_meta, "kind", "") or "") if agg_meta else ""
    order.delivery_eta_rule_code = str(getattr(agg_meta, "rule_code", "") or "") if agg_meta else ""
    order.delivery_eta_source = str(getattr(agg_meta, "source", "") or "") if agg_meta else ""
    order.save(update_fields=[
        "delivery_min_date", "delivery_max_date", "delivery_eta_kind",
        "delivery_eta_rule_code", "delivery_eta_source", "updated_at",
    ])
