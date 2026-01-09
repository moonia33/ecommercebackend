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
    if shipping_method == "lpexpress":
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
        .select_related("variant")
        .order_by("id")
    )
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


def expire_pending_payment_reservations(*, now=None) -> int:
    from checkout.models import Order, PaymentIntent

    now = now or timezone.now()

    ttl_gateway_min = int(getattr(settings, "INVENTORY_RESERVATION_TTL_MINUTES_GATEWAY", 30) or 30)
    ttl_bank_hours = int(getattr(settings, "INVENTORY_RESERVATION_TTL_HOURS_BANK_TRANSFER", 72) or 72)

    cutoff_gateway = now - timedelta(minutes=ttl_gateway_min)
    cutoff_bank = now - timedelta(hours=ttl_bank_hours)

    qs = (
        Order.objects.filter(status=Order.Status.PENDING_PAYMENT)
        .select_related("payment_intent")
        .order_by("id")
    )

    expired_ids: list[int] = []
    for o in qs:
        pi = getattr(o, "payment_intent", None)
        provider = (getattr(pi, "provider", "") or "").strip()
        if provider == PaymentIntent.Provider.BANK_TRANSFER:
            if o.created_at < cutoff_bank:
                expired_ids.append(o.id)
        else:
            if o.created_at < cutoff_gateway:
                expired_ids.append(o.id)

    if not expired_ids:
        return 0

    expired = 0
    with transaction.atomic():
        for oid in expired_ids:
            o = Order.objects.select_for_update().select_related("payment_intent").filter(id=oid).first()
            if not o:
                continue
            if o.status != Order.Status.PENDING_PAYMENT:
                continue

            release_inventory_for_order(order_id=o.id)
            o.status = Order.Status.CANCELLED
            o.save(update_fields=["status", "updated_at"])

            pi = getattr(o, "payment_intent", None)
            if pi and pi.status not in {PaymentIntent.Status.SUCCEEDED, PaymentIntent.Status.CANCELLED}:
                pi.status = PaymentIntent.Status.CANCELLED
                pi.save(update_fields=["status", "updated_at"])

            expired += 1

    return expired
