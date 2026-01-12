from __future__ import annotations

from decimal import Decimal
from datetime import date

from django.conf import settings
from django.db import models
from django.db import transaction
from django.utils import timezone
from ninja import Router
from ninja.errors import HttpError

from accounts.auth import JWTAuth
from accounts.models import UserAddress
from catalog.models import Category, InventoryItem, Variant
from pricing.services import get_vat_rate
from promotions.models import Coupon
from promotions.services import apply_promo_to_unit_net
from shipping.services import estimate_delivery_window

from analytics.services import track_event

from .models import Cart, CartItem, Order, OrderConsent, OrderDiscount, OrderFee, OrderLine, PaymentIntent
from .schemas import (
    CartItemAddIn,
    CartItemOut,
    CartItemUpdateIn,
    CartOut,
    ConsentDefinitionOut,
    CheckoutConfirmIn,
    CheckoutConfirmOut,
    CheckoutPreviewIn,
    CheckoutPreviewOut,
    MoneyOut,
    OrderOut,
    OrderLineOut,
    PaymentMethodOut,
    FeeOut,
    ShippingMethodOut,
)
from .services import (
    calculate_fee_money,
    calculate_fees,
    get_shipping_net,
    get_shipping_tax_class,
    inventory_available_for_variant,
    inventory_available_for_offer,
    money_from_net,
    reserve_inventory_for_order,
)


router = Router(tags=["checkout"])


def _dw_value(dw, key: str):
    try:
        if dw is None:
            return None
        if hasattr(dw, "get"):
            return dw.get(key)
        return getattr(dw, key, None)
    except Exception:
        return None
_auth = JWTAuth()


def _bank_transfer_instructions_for(*, order_id: int | None, country_code: str) -> str:
    country_code = (country_code or "").strip().upper()
    try:
        from payments.models import PaymentMethod

        pm = (
            PaymentMethod.objects.filter(is_active=True, code="bank_transfer")
            .filter(models.Q(country_code="") | models.Q(country_code=country_code))
            .order_by("-country_code")
            .first()
        )
        if pm:
            return pm.instructions_for_order(order_id=order_id)
    except Exception:
        pass

    return (getattr(settings, "BANK_TRANSFER_INSTRUCTIONS", "") or "").strip()


@router.get("/consents", response=list[ConsentDefinitionOut], auth=_auth)
def checkout_consents(request):
    _require_user(request)
    return [
        ConsentDefinitionOut(
            kind="terms",
            name="Pirkimo sąlygos",
            document_version=getattr(settings, "CHECKOUT_TERMS_VERSION", "v1"),
            required=True,
            url=getattr(settings, "CHECKOUT_TERMS_URL", "/terms"),
        ),
        ConsentDefinitionOut(
            kind="privacy",
            name="Privatumo politika",
            document_version=getattr(
                settings, "CHECKOUT_PRIVACY_VERSION", "v1"),
            required=True,
            url=getattr(settings, "CHECKOUT_PRIVACY_URL", "/privacy"),
        ),
    ]


@router.get("/payment-methods", response=list[PaymentMethodOut], auth=_auth)
def payment_methods(request, country_code: str = "LT"):
    _require_user(request)
    country_code = (country_code or "").strip().upper() or "LT"
    try:
        from payments.models import PaymentMethod

        methods = list(
            PaymentMethod.objects.filter(is_active=True)
            .filter(
                models.Q(country_code="") | models.Q(country_code=country_code)
            )
            .order_by("sort_order", "code")
        )
    except Exception:
        methods = []

    if methods:
        out: list[PaymentMethodOut] = []
        for m in methods:
            out.append(
                PaymentMethodOut(
                    code=m.code,
                    name=m.name,
                    kind=m.kind,
                    provider=m.provider,
                    instructions=(m.instructions or "").strip(),
                )
            )
        return out

    # Fallback (dev / before DB config exists)
    instructions = (getattr(settings, "BANK_TRANSFER_INSTRUCTIONS", "") or "").strip()
    return [
        PaymentMethodOut(
            code="bank_transfer",
            name="Paprastas pavedimas",
            kind="offline",
            provider="bank_transfer",
            instructions=instructions,
        ),
        PaymentMethodOut(
            code="klix",
            name="Bankinis mokėjimas (Klix)",
            kind="gateway",
            provider="klix",
            instructions="",
        ),
    ]


def _require_user(request):
    user = request.auth
    if not user:
        raise HttpError(401, "Unauthorized")
    return user


def _user_from_cookie_if_present(request):
    # Cart endpoints must work without auth; but if access cookie is present, use it.

    # Allow normal Django session auth if present
    try:
        u = getattr(request, "user", None)
        if u is not None and getattr(u, "is_authenticated", False):
            return u
    except Exception:
        pass

    try:
        from accounts.jwt_utils import decode_token
        from django.contrib.auth import get_user_model
        from django.conf import settings

        cookie_name = getattr(settings, "AUTH_COOKIE_ACCESS_NAME", "access_token")
        token = (request.COOKIES.get(cookie_name) or "").strip()
        if not token:
            return None

        payload = decode_token(token)
        if payload.get("type") != "access":
            return None
        user_id = payload.get("sub")
        if not user_id:
            return None
        User = get_user_model()
        return User.objects.get(id=int(user_id), is_active=True)
    except Exception:
        return None


def _get_cart_for_request(request, *, create: bool) -> Cart | None:
    user = _user_from_cookie_if_present(request)

    session_key = ""
    if hasattr(request, "session"):
        try:
            session_key = request.session.session_key or ""
        except Exception:
            session_key = ""

    if user:
        user_cart = getattr(user, "cart", None)

        guest_cart = None
        if session_key:
            guest_cart = Cart.objects.filter(
                user=None, session_key=session_key).first()

        if not user_cart:
            # Allow merge on first GET after login if a guest cart exists.
            if create or guest_cart:
                user_cart, _ = Cart.objects.get_or_create(
                    user=user, defaults={"session_key": ""})
            else:
                return None

        # If a guest cart exists for this session, merge/attach it.
        if guest_cart and guest_cart.id != user_cart.id:
            with transaction.atomic():
                for it in CartItem.objects.filter(cart=guest_cart).select_related("variant", "offer"):
                    if it.offer_id:
                        existing = CartItem.objects.filter(
                            cart=user_cart, offer_id=it.offer_id
                        ).first()
                    else:
                        existing = CartItem.objects.filter(
                            cart=user_cart, variant=it.variant, offer__isnull=True
                        ).first()
                    if existing:
                        existing.qty = existing.qty + it.qty
                        existing.save(update_fields=["qty", "updated_at"])
                    else:
                        it.pk = None
                        it.cart = user_cart
                        it.save()
                CartItem.objects.filter(cart=guest_cart).delete()
                guest_cart.delete()

        return user_cart

    # Guest: only create session/cart on write.
    if not session_key and create and hasattr(request, "session"):
        request.session["cart_init"] = True
        try:
            request.session.save()
        except Exception:
            pass
        try:
            session_key = request.session.session_key or ""
        except Exception:
            session_key = ""

    if not session_key:
        return None

    if not create:
        return Cart.objects.filter(user=None, session_key=session_key).first()

    cart, _ = Cart.objects.get_or_create(user=None, session_key=session_key)
    return cart


def _country_code_from_address(addr: UserAddress) -> str:
    cc = (addr.country_code or "").strip().upper()
    if len(cc) != 2:
        return "LT"
    return cc


def _resolve_pickup_dpd(*, pickup_point_id: str, country_code: str):
    from dpd.models import DpdLocker

    locker = DpdLocker.objects.filter(locker_id=pickup_point_id, is_active=True).first()
    if not locker:
        raise HttpError(400, "Invalid pickup_point_id")
    if locker.country_code and country_code and locker.country_code.upper() != country_code:
        raise HttpError(400, "pickup_point_id country mismatch")
    return locker, None


def _resolve_pickup_unisend(*, pickup_point_id: str, country_code: str):
    from unisend.models import UnisendTerminal

    terminal = UnisendTerminal.objects.filter(terminal_id=pickup_point_id, is_active=True).first()
    if not terminal:
        raise HttpError(400, "Invalid pickup_point_id")
    if terminal.country_code and country_code and terminal.country_code.upper() != country_code:
        raise HttpError(400, "pickup_point_id country mismatch")

    snapshot = {
        "pickup_point_id": str(terminal.terminal_id or "").strip(),
        "pickup_point_name": str(terminal.name or "").strip(),
        "pickup_point_raw": terminal.raw or {},
    }

    # For Unisend we don't have a dedicated FK on Order yet (pickup_locker points to dpd.DpdLocker).
    return None, snapshot


PICKUP_POINT_RESOLVERS = {
    "dpd": _resolve_pickup_dpd,
    "lpexpress": _resolve_pickup_unisend,
}


def _validate_and_resolve_pickup(*, shipping_method: str, pickup_point_id: str | None, country_code: str):
    from shipping.models import ShippingMethod

    shipping_method = (shipping_method or "").strip() or "lpexpress"
    pickup_point_id = (pickup_point_id or "").strip() or None
    country_code = (country_code or "").strip().upper()

    method = ShippingMethod.objects.filter(
        code=shipping_method, is_active=True).first()
    requires_pickup = bool(
        getattr(method, "requires_pickup_point", False)) if method else False
    carrier_code = (getattr(method, "carrier_code", "")
                    or "") if method else ""

    if not requires_pickup:
        return carrier_code, None, None

    if not pickup_point_id:
        raise HttpError(
            400, "pickup_point_id is required for this shipping_method")

    carrier = (carrier_code or "").lower()
    resolver = PICKUP_POINT_RESOLVERS.get(carrier)
    if not resolver:
        raise HttpError(400, "pickup_point_id is not supported for this carrier")

    locker, snapshot = resolver(pickup_point_id=pickup_point_id, country_code=country_code)
    return carrier_code, locker, snapshot


def _maybe_fill_pickup_point_id_from_user(*, user, shipping_method: str, pickup_point_id: str | None):
    pickup_point_id = (pickup_point_id or "").strip() or None
    if pickup_point_id:
        return pickup_point_id

    try:
        pref = getattr(user, "primary_pickup_point", None)
    except Exception:
        pref = None
    if not pref:
        return None

    if (getattr(pref, "shipping_method_code", "") or "").strip() != (shipping_method or "").strip():
        return None

    pid = (getattr(pref, "pickup_point_id", "") or "").strip() or None
    return pid


def _variant_money(*, variant: Variant, country_code: str, qty: int) -> tuple[MoneyOut, MoneyOut, Decimal]:
    product = variant.product
    if not product or not product.tax_class_id:
        raise HttpError(400, "Product has no tax_class assigned")

    try:
        vat_rate = get_vat_rate(country_code=country_code,
                                tax_class=product.tax_class)
    except LookupError:
        raise HttpError(400, "VAT rate not configured for country/tax_class")
    except ValueError as exc:
        raise HttpError(400, str(exc))

    unit_net = Decimal(variant.price_eur)
    unit = money_from_net(currency="EUR", unit_net=unit_net,
                          vat_rate=vat_rate, qty=1)
    total = money_from_net(
        currency="EUR", unit_net=unit_net, vat_rate=vat_rate, qty=qty)

    return (
        MoneyOut(currency=unit.currency, net=unit.net,
                 vat_rate=unit.vat_rate, vat=unit.vat, gross=unit.gross),
        MoneyOut(currency=total.currency, net=total.net,
                 vat_rate=total.vat_rate, vat=total.vat, gross=total.gross),
        vat_rate,
    )


def _effective_offer_unit_net(*, list_unit_net: Decimal, offer: InventoryItem) -> Decimal:
    if bool(getattr(offer, "never_discount", False)):
        return Decimal(list_unit_net)

    if offer.offer_price_override_eur is not None:
        return Decimal(offer.offer_price_override_eur)
    if offer.offer_discount_percent is not None:
        pct = int(offer.offer_discount_percent)
        pct = max(0, min(100, pct))
        return (Decimal(list_unit_net) * (Decimal(100 - pct) / Decimal(100))).quantize(Decimal("0.01"))
    return Decimal(list_unit_net)


def _discount_percent(*, list_unit_net: Decimal, sale_unit_net: Decimal) -> int | None:
    list_unit_net = Decimal(list_unit_net)
    sale_unit_net = Decimal(sale_unit_net)
    if list_unit_net <= 0:
        return None
    if sale_unit_net >= list_unit_net:
        return None
    pct = int(((list_unit_net - sale_unit_net) / list_unit_net * Decimal(100)).quantize(Decimal("1")))
    return max(0, min(100, pct))


def _cart_item_money(
    *,
    item: CartItem,
    country_code: str,
    channel: str = "normal",
    customer_group_id: int | None = None,
) -> tuple[MoneyOut, MoneyOut, MoneyOut | None, int | None, Decimal]:
    v = item.variant
    product = v.product
    if not product or not product.tax_class_id:
        raise HttpError(400, "Product has no tax_class assigned")

    try:
        vat_rate = get_vat_rate(country_code=country_code,
                                tax_class=product.tax_class)
    except LookupError:
        raise HttpError(400, "VAT rate not configured for country/tax_class")
    except ValueError as exc:
        raise HttpError(400, str(exc))

    list_unit_net = Decimal(v.price_eur)
    base_unit_net = (
        _effective_offer_unit_net(list_unit_net=list_unit_net, offer=item.offer)
        if getattr(item, "offer_id", None)
        else list_unit_net
    )

    is_discounted_offer = bool(
        item.offer_id
        and item.offer
        and (not bool(getattr(item.offer, "never_discount", False)))
        and (
            item.offer.offer_price_override_eur is not None
            or item.offer.offer_discount_percent is not None
        )
    )

    unit_net, _rule = apply_promo_to_unit_net(
        base_unit_net=base_unit_net,
        channel=channel,
        category_id=(v.product.category_id if v.product_id else None),
        brand_id=(v.product.brand_id if v.product_id else None),
        product_id=(v.product_id if v.product_id else None),
        variant_id=v.id,
        customer_group_id=customer_group_id,
        allow_additional_promotions=bool(getattr(item.offer, "allow_additional_promotions", False)) if item.offer_id else False,
        is_discounted_offer=is_discounted_offer,
    )

    unit = money_from_net(currency="EUR", unit_net=unit_net,
                          vat_rate=vat_rate, qty=1)
    total = money_from_net(
        currency="EUR", unit_net=unit_net, vat_rate=vat_rate, qty=int(item.qty))

    compare_at = None
    disc_pct = _discount_percent(list_unit_net=list_unit_net, sale_unit_net=unit_net)
    if disc_pct is not None:
        base = money_from_net(currency="EUR", unit_net=list_unit_net, vat_rate=vat_rate, qty=1)
        compare_at = MoneyOut(currency=base.currency, net=base.net, vat_rate=base.vat_rate, vat=base.vat, gross=base.gross)

    return (
        MoneyOut(currency=unit.currency, net=unit.net, vat_rate=unit.vat_rate, vat=unit.vat, gross=unit.gross),
        MoneyOut(currency=total.currency, net=total.net, vat_rate=total.vat_rate, vat=total.vat, gross=total.gross),
        compare_at,
        disc_pct,
        vat_rate,
    )


def _serialize_cart_items(
    *,
    items: list[CartItem],
    country_code: str,
    channel: str = "normal",
    customer_group_id: int | None = None,
) -> tuple[list[CartItemOut], MoneyOut, dict | None]:
    out_items: list[CartItemOut] = []

    agg_min = None
    agg_max = None
    agg_meta: dict | None = None

    total_net = Decimal("0.00")
    total_vat = Decimal("0.00")
    total_gross = Decimal("0.00")

    for it in items:
        v = it.variant
        unit_price, line_total, compare_at, disc_pct, _vat_rate = _cart_item_money(
            item=it,
            country_code=country_code,
            channel=channel,
            customer_group_id=customer_group_id,
        )

        if it.offer_id:
            stock_available = inventory_available_for_offer(offer_id=it.offer_id)
        else:
            stock_available = inventory_available_for_variant(variant_id=v.id)

        total_net += line_total.net
        total_vat += line_total.vat
        total_gross += line_total.gross

        out_items.append(
            CartItemOut(
                id=it.id,
                variant_id=v.id,
                offer_id=(int(it.offer_id) if it.offer_id else None),
                sku=v.sku,
                name=(v.product.name if v.product_id else v.sku),
                qty=it.qty,
                stock_available=int(stock_available),
                unit_price=unit_price,
                compare_at_price=compare_at,
                discount_percent=disc_pct,
                line_total=line_total,
                delivery_window=None,
            )
        )

        # Per-item delivery window
        dw = None
        try:
            product = v.product
            dw = estimate_delivery_window(
                now=timezone.now(),
                country_code=country_code,
                channel=channel,
                warehouse_id=int(it.offer.warehouse_id) if getattr(it, "offer_id", None) and it.offer and it.offer.warehouse_id else None,
                product_id=int(product.id) if product else None,
                brand_id=int(product.brand_id) if product and product.brand_id else None,
                category_id=int(product.category_id) if product and product.category_id else None,
                product_group_id=int(getattr(product, "group_id", None)) if product and getattr(product, "group_id", None) else None,
            )
        except Exception:
            dw = None

        if dw is not None:
            dw_out = {
                "min_date": dw.min_date.isoformat(),
                "max_date": dw.max_date.isoformat(),
                "kind": dw.kind,
                "rule_code": dw.rule_code,
                "source": dw.source,
            }
            out_items[-1].delivery_window = dw_out

            if agg_min is None or dw.min_date > agg_min:
                agg_min = dw.min_date
            if agg_max is None or dw.max_date > agg_max:
                agg_max = dw.max_date
            agg_meta = dw_out

    items_total = MoneyOut(
        currency="EUR",
        net=total_net,
        vat_rate=Decimal("0"),
        vat=total_vat,
        gross=total_gross,
    )
    agg_out = None
    if agg_min is not None and agg_max is not None:
        agg_out = {
            "min_date": agg_min.isoformat(),
            "max_date": agg_max.isoformat(),
            "kind": (str((agg_meta or {}).get("kind") or "estimated")),
            "rule_code": str((agg_meta or {}).get("rule_code") or ""),
            "source": str((agg_meta or {}).get("source") or ""),
        }

    return out_items, items_total, agg_out


@router.get("/cart", response=CartOut)
def get_cart(request, country_code: str = "LT", channel: str = "normal"):
    country_code = (country_code or "").strip().upper()
    if len(country_code) != 2:
        raise HttpError(400, "Invalid country_code")

    channel = (channel or "normal").strip().lower()
    if channel not in {"normal", "outlet"}:
        raise HttpError(400, "Invalid channel")

    cart = _get_cart_for_request(request, create=False)
    if cart is None:
        return CartOut(
            country_code=country_code,
            items=[],
            items_total=MoneyOut(
                currency="EUR",
                net=Decimal("0.00"),
                vat_rate=Decimal("0"),
                vat=Decimal("0.00"),
                gross=Decimal("0.00"),
            ),
        )
    items = list(
        CartItem.objects.select_related(
            "variant", "variant__product", "variant__product__tax_class", "offer")
        .filter(cart=cart)
        .order_by("id")
    )

    if items:
        try:
            track_event(
                request=request,
                name="view_cart",
                object_type="cart",
                object_id=int(cart.id),
                payload={"items_count": len(items)},
                country_code=country_code,
                channel=channel,
                language_code="",
            )
        except Exception:
            pass

    out_items, items_total, delivery_window = _serialize_cart_items(
        items=items,
        country_code=country_code,
        channel=channel,
    )
    return CartOut(
        country_code=country_code,
        items=out_items,
        items_total=items_total,
        delivery_window=delivery_window,
    )


@router.post("/cart/items", response=CartOut)
def add_cart_item(request, payload: CartItemAddIn, country_code: str = "LT"):
    qty = int(payload.qty or 0)
    if qty <= 0:
        raise HttpError(400, "qty must be positive")

    variant = (
        Variant.objects.select_related("product", "product__tax_class")
        .filter(id=payload.variant_id, is_active=True, product__is_active=True)
        .first()
    )
    if not variant:
        raise HttpError(404, "Variant not found")

    offer = None
    if getattr(payload, "offer_id", None):
        offer = (
            InventoryItem.objects.filter(
                id=int(payload.offer_id),
                variant_id=variant.id,
            )
            .select_related("warehouse")
            .first()
        )
        if not offer:
            raise HttpError(404, "Offer not found")
    else:
        # Default behaviour: allocate qty across offers in priority order.
        # This ensures e.g. returned stock (with special pricing) is sold first,
        # but any remainder falls back to other warehouses/offers.
        candidates = list(
            InventoryItem.objects.filter(
                variant_id=variant.id,
                offer_visibility=InventoryItem.OfferVisibility.NORMAL,
                qty_on_hand__gt=models.F("qty_reserved"),
            ).order_by("id")
        )
        candidates.sort(
            key=lambda ii: (
                -int(ii.offer_priority or 0),
                _effective_offer_unit_net(list_unit_net=Decimal(variant.price_eur), offer=ii),
                int(ii.id),
            )
        )

    cart = _get_cart_for_request(request, create=True)
    if cart is None:
        raise HttpError(400, "Session is not available")

    with transaction.atomic():
        # Variant-level cap: do not allow cart to exceed available inventory (all warehouses/offers).
        existing_variant_qty = (
            CartItem.objects.filter(cart=cart, variant_id=variant.id)
            .aggregate(total=models.Sum("qty"))
            .get("total")
            or 0
        )
        variant_available = inventory_available_for_variant(variant_id=variant.id)
        if int(existing_variant_qty) + int(qty) > int(variant_available):
            raise HttpError(409, "Not enough stock")

        # Explicit offer_id: keep existing semantics (single offer line).
        if offer is not None:
            inv = InventoryItem.objects.select_for_update().filter(id=int(offer.id)).first()
            if not inv:
                raise HttpError(404, "Offer not found")

            item = CartItem.objects.filter(cart=cart, offer=offer).first()
            desired_qty = int(qty) + (int(item.qty) if item else 0)
            offer_available = max(0, int(inv.qty_on_hand) - int(inv.qty_reserved))
            if desired_qty > offer_available:
                raise HttpError(409, "Not enough stock")

            if item:
                item.qty = desired_qty
                item.save(update_fields=["qty", "updated_at"])
            else:
                CartItem.objects.create(cart=cart, variant=variant, offer=offer, qty=int(qty))
        else:
            remaining = int(qty)

            # Allocate across offer candidates (available only).
            offer_ids = [int(c.id) for c in candidates]
            existing_offer_qty = {
                int(r["offer_id"]): int(r["total"] or 0)
                for r in CartItem.objects.filter(cart=cart, offer_id__in=offer_ids)
                .values("offer_id")
                .annotate(total=models.Sum("qty"))
            }

            # Lock candidate inventory rows while allocating.
            inv_locked = {
                int(i.id): i
                for i in InventoryItem.objects.select_for_update().filter(id__in=offer_ids)
            }

            for cand in candidates:
                if remaining <= 0:
                    break
                inv = inv_locked.get(int(cand.id))
                if not inv:
                    continue
                available = max(0, int(inv.qty_on_hand) - int(inv.qty_reserved))
                available -= int(existing_offer_qty.get(int(cand.id), 0))
                if available <= 0:
                    continue

                add_qty = min(available, remaining)
                existing = CartItem.objects.filter(cart=cart, offer=cand).first()
                if existing:
                    existing.qty = int(existing.qty) + int(add_qty)
                    existing.save(update_fields=["qty", "updated_at"])
                else:
                    CartItem.objects.create(cart=cart, variant=variant, offer=cand, qty=int(add_qty))
                remaining -= int(add_qty)

            if remaining > 0:
                raise HttpError(409, "Not enough stock")

    try:
        track_event(
            request=request,
            name="add_to_cart",
            object_type="variant",
            object_id=int(variant.id),
            payload={
                "qty": int(qty),
                "product_id": int(getattr(getattr(variant, "product", None), "id", 0) or 0),
                "product_slug": str(getattr(getattr(variant, "product", None), "slug", "") or ""),
                "sku": str(getattr(variant, "sku", "") or ""),
            },
            country_code=country_code,
            channel="normal",
        )
    except Exception:
        pass

    return get_cart(request, country_code=country_code)


@router.patch("/cart/items/{item_id}", response=CartOut)
def update_cart_item(request, item_id: int, payload: CartItemUpdateIn, country_code: str = "LT"):
    qty = int(payload.qty or 0)

    cart = _get_cart_for_request(request, create=False)
    if cart is None:
        raise HttpError(404, "Cart item not found")
    item = (
        CartItem.objects.select_related(
            "variant", "variant__product", "variant__product__tax_class", "offer")
        .filter(cart=cart, id=item_id)
        .first()
    )
    if not item:
        raise HttpError(404, "Cart item not found")

    prev_qty = int(getattr(item, "qty", 0) or 0)

    with transaction.atomic():
        if qty <= 0:
            item.delete()
        else:
            # Variant-level cap (sum of all cart lines for this variant).
            other_qty = (
                CartItem.objects.filter(cart=cart, variant_id=item.variant_id)
                .exclude(id=item.id)
                .aggregate(total=models.Sum("qty"))
                .get("total")
                or 0
            )
            variant_available = inventory_available_for_variant(variant_id=item.variant_id)
            if int(other_qty) + int(qty) > int(variant_available):
                raise HttpError(409, "Not enough stock")

            # Offer-level cap (if offer is selected, quantity can't exceed that offer's availability).
            if getattr(item, "offer_id", None):
                inv = InventoryItem.objects.select_for_update().filter(id=int(item.offer_id)).first()
                if not inv:
                    raise HttpError(409, "Not enough stock")
                offer_available = max(0, int(inv.qty_on_hand) - int(inv.qty_reserved))
                if int(qty) > int(offer_available):
                    raise HttpError(409, "Not enough stock")

            item.qty = qty
            item.save(update_fields=["qty", "updated_at"])

    try:
        name = "add_to_cart" if qty > prev_qty else "remove_from_cart"
        delta = abs(int(qty) - int(prev_qty))
        if delta > 0:
            track_event(
                request=request,
                name=name,
                object_type="variant",
                object_id=int(item.variant_id),
                payload={
                    "qty": int(delta),
                    "product_id": int(getattr(getattr(item.variant, "product", None), "id", 0) or 0),
                    "product_slug": str(getattr(getattr(item.variant, "product", None), "slug", "") or ""),
                    "sku": str(getattr(item.variant, "sku", "") or ""),
                },
                country_code=country_code,
                channel="normal",
            )
    except Exception:
        pass

    return get_cart(request, country_code=country_code)


@router.delete("/cart/items/{item_id}", response=CartOut)
def delete_cart_item(request, item_id: int, country_code: str = "LT"):
    cart = _get_cart_for_request(request, create=False)
    if cart is None:
        raise HttpError(404, "Cart item not found")

    item = (
        CartItem.objects.select_related("variant", "variant__product")
        .filter(cart=cart, id=item_id)
        .first()
    )
    if not item:
        raise HttpError(404, "Cart item not found")

    prev_qty = int(getattr(item, "qty", 0) or 0)
    deleted = item.delete()[0]
    if not deleted:
        raise HttpError(404, "Cart item not found")

    try:
        track_event(
            request=request,
            name="remove_from_cart",
            object_type="variant",
            object_id=int(item.variant_id),
            payload={
                "qty": int(prev_qty),
                "product_id": int(getattr(getattr(item.variant, "product", None), "id", 0) or 0),
                "product_slug": str(getattr(getattr(item.variant, "product", None), "slug", "") or ""),
                "sku": str(getattr(item.variant, "sku", "") or ""),
            },
            country_code=country_code,
            channel="normal",
        )
    except Exception:
        pass

    return get_cart(request, country_code=country_code)


@router.get("/shipping-methods", response=list[ShippingMethodOut], auth=_auth)
def shipping_methods(request, country_code: str = "LT"):
    _require_user(request)
    country_code = (country_code or "").strip().upper()
    if len(country_code) != 2:
        raise HttpError(400, "Invalid country_code")

    from shipping.models import ShippingMethod, ShippingRate

    tax_class = get_shipping_tax_class()
    if not tax_class:
        vat_rate = Decimal("0")
    else:
        vat_rate = get_vat_rate(country_code=country_code, tax_class=tax_class)

    methods = list(
        ShippingMethod.objects.filter(
            is_active=True).order_by("sort_order", "code")
    )
    rates = {
        r.method_id: r
        for r in ShippingRate.objects.filter(
            is_active=True, method__in=methods, country_code=country_code
        ).select_related("method")
    }

    out: list[ShippingMethodOut] = []
    for m in methods:
        shipping_net = Decimal("0.00")
        if m.id in rates:
            shipping_net = Decimal(rates[m.id].net_eur)
        else:
            # Backward-compatible fallback: allow lpexpress via env even without DB config.
            try:
                shipping_net = get_shipping_net(
                    shipping_method=m.code, country_code=country_code
                )
            except ValueError:
                shipping_net = Decimal("0.00")

        price = money_from_net(
            currency="EUR", unit_net=shipping_net, vat_rate=vat_rate, qty=1)
        out.append(
            ShippingMethodOut(
                code=m.code,
                name=m.name,
                carrier_code=getattr(m, "carrier_code", "") or "",
                requires_pickup_point=bool(
                    getattr(m, "requires_pickup_point", False)),
                price=MoneyOut(
                    currency=price.currency,
                    net=price.net,
                    vat_rate=price.vat_rate,
                    vat=price.vat,
                    gross=price.gross,
                ),
            )
        )

    return out


@router.post("/checkout/preview", response=CheckoutPreviewOut, auth=_auth)
def checkout_preview(request, payload: CheckoutPreviewIn):
    user = _require_user(request)

    addr = UserAddress.objects.filter(
        user=user, id=payload.shipping_address_id).first()
    if not addr:
        raise HttpError(404, "Shipping address not found")

    shipping_method = (payload.shipping_method or "").strip() or "lpexpress"
    payment_method = (getattr(payload, "payment_method", "") or "").strip() or "klix"
    channel = (getattr(payload, "channel", "normal") or "normal").strip().lower()
    if channel not in {"normal", "outlet"}:
        raise HttpError(400, "Invalid channel")

    coupon_code = (getattr(payload, "coupon_code", None) or "").strip().lower() or None

    country_code = _country_code_from_address(addr)

    pickup_point_id = _maybe_fill_pickup_point_id_from_user(
        user=user,
        shipping_method=shipping_method,
        pickup_point_id=getattr(payload, "pickup_point_id", None),
    )

    # Validate pickup-point selection early (even though we don't persist anything on preview).
    _validate_and_resolve_pickup(
        shipping_method=shipping_method,
        pickup_point_id=pickup_point_id,
        country_code=country_code,
    )

    cart = _get_cart_for_request(request, create=False)
    if cart is None:
        raise HttpError(400, "Cart is empty")
    items = list(
        CartItem.objects.select_related(
            "variant", "variant__product", "variant__product__tax_class", "offer")
        .filter(cart=cart)
        .order_by("id")
    )
    if not items:
        raise HttpError(400, "Cart is empty")

    try:
        track_event(
            request=request,
            name="begin_checkout",
            object_type="cart",
            object_id=int(cart.id),
            payload={"items_count": len(items)},
            country_code=country_code,
            channel=channel,
            language_code="",
        )
    except Exception:
        pass

    coupon = None
    if coupon_code:
        if channel not in set(getattr(settings, "COUPON_ALLOWED_CHANNELS", ["normal"])):
            raise HttpError(400, "Coupon is not allowed for this channel")

        primary = user.get_primary_customer_group() if user else None
        if primary and not bool(getattr(primary, "allow_coupons", True)):
            raise HttpError(400, "Coupons are not allowed for this customer")

        coupon = Coupon.objects.filter(code=coupon_code).first()
        if not coupon or not coupon.is_valid_now():
            raise HttpError(400, "Invalid coupon")

        # Usage limits are counted only when orders are PAID, but we still validate against
        # already redeemed usage here.
        if coupon.usage_limit_total is not None and int(coupon.times_redeemed) >= int(coupon.usage_limit_total):
            raise HttpError(400, "Coupon usage limit reached")
        if coupon.usage_limit_per_user is not None:
            from promotions.models import CouponRedemption

            used = CouponRedemption.objects.filter(coupon=coupon, user=user).count()
            if int(used) >= int(coupon.usage_limit_per_user):
                raise HttpError(400, "Coupon usage limit reached for user")

    # Coupon discount (applies only to cart/items total)
    discount_net = Decimal("0.00")
    discount_vat = Decimal("0.00")
    discount_gross = Decimal("0.00")

    # Stock check
    for it in items:
        if getattr(it, "offer_id", None):
            available = inventory_available_for_offer(offer_id=it.offer_id)
        else:
            available = inventory_available_for_variant(variant_id=it.variant_id)
        if int(available) < int(it.qty):
            raise HttpError(409, f"Not enough stock for {it.variant.sku}")

    primary = user.get_primary_customer_group() if user else None
    customer_group_id = int(primary.id) if primary else None

    out_items, items_total, delivery_window = _serialize_cart_items(
        items=items,
        country_code=country_code,
        channel=channel,
        customer_group_id=customer_group_id,
    )

    if coupon:
        eligible_items_net = Decimal("0.00")
        eligible_items_vat = Decimal("0.00")

        for it in items:
            _unit, line_total, _compare_at, _disc_pct, _vat_rate = _cart_item_money(
                item=it,
                country_code=country_code,
                channel=channel,
                customer_group_id=customer_group_id,
            )

            if it.offer and bool(getattr(it.offer, "never_discount", False)):
                continue

            is_discounted_offer = bool(
                it.offer_id
                and it.offer
                and (
                    (not bool(getattr(it.offer, "never_discount", False)))
                    and (
                        it.offer.offer_price_override_eur is not None
                        or it.offer.offer_discount_percent is not None
                    )
                )
            )
            # If compare_at is present, the unit price was reduced vs base (offer-adjusted)
            # price - this indicates a promo discount for that line.
            is_promo_discounted_line = bool(_compare_at)
            allow_stack_for_line = bool(
                coupon.apply_on_discounted_items
            )
            if (is_discounted_offer or is_promo_discounted_line) and not allow_stack_for_line:
                continue

            eligible_items_net += Decimal(line_total.net)
            eligible_items_vat += Decimal(line_total.vat)

        discount_net = coupon.get_discount_net_for(eligible_items_net=eligible_items_net)
        if discount_net:
            eff_rate = (eligible_items_vat / eligible_items_net) if eligible_items_net else Decimal("0")
            discount_vat = (discount_net * eff_rate).quantize(Decimal("0.01"))
            discount_gross = (discount_net + discount_vat).quantize(Decimal("0.01"))

        if discount_net <= 0 and not coupon.is_free_shipping_for(shipping_method=shipping_method):
            raise HttpError(400, "Coupon is not applicable to cart items")

    try:
        shipping_net = get_shipping_net(shipping_method=shipping_method, country_code=country_code)
    except ValueError:
        raise HttpError(400, "Unsupported shipping_method")

    if coupon and coupon.is_free_shipping_for(shipping_method=shipping_method):
        shipping_net = Decimal("0.00")
    tax_class = get_shipping_tax_class()
    if not tax_class:
        shipping_vat_rate = Decimal("0")
    else:
        shipping_vat_rate = get_vat_rate(
            country_code=country_code, tax_class=tax_class)
    shipping_money = money_from_net(
        currency="EUR", unit_net=shipping_net, vat_rate=shipping_vat_rate, qty=1)

    fee_pairs = calculate_fees(
        currency="EUR",
        country_code=country_code,
        items_gross=items_total.gross,
        payment_method=payment_method,
    )
    fees_out: list[FeeOut] = []
    fees_net = Decimal("0.00")
    fees_vat = Decimal("0.00")
    fees_gross = Decimal("0.00")
    for rule, m in fee_pairs:
        fees_net += m.net
        fees_vat += m.vat
        fees_gross += m.gross
        fees_out.append(
            FeeOut(
                code=rule.code,
                name=rule.name,
                amount=MoneyOut(
                    currency=m.currency,
                    net=m.net,
                    vat_rate=m.vat_rate,
                    vat=m.vat,
                    gross=m.gross,
                ),
            )
        )

    order_net = items_total.net + shipping_money.net + fees_net - discount_net
    order_vat = items_total.vat + shipping_money.vat + fees_vat - discount_vat
    order_gross = items_total.gross + shipping_money.gross + fees_gross - discount_gross

    shipping_total = MoneyOut(
        currency="EUR",
        net=shipping_money.net,
        vat_rate=shipping_money.vat_rate,
        vat=shipping_money.vat,
        gross=shipping_money.gross,
    )
    discount_total = MoneyOut(
        currency="EUR",
        net=discount_net,
        vat_rate=Decimal("0"),
        vat=discount_vat,
        gross=discount_gross,
    )
    fees_total = MoneyOut(currency="EUR", net=fees_net, vat_rate=Decimal("0"), vat=fees_vat, gross=fees_gross)
    order_total = MoneyOut(currency="EUR", net=order_net, vat_rate=Decimal(
        "0"), vat=order_vat, gross=order_gross)

    return CheckoutPreviewOut(
        country_code=country_code,
        shipping_method=shipping_method,
        items=out_items,
        delivery_window=delivery_window,
        items_total=items_total,
        discount_total=discount_total,
        shipping_total=shipping_total,
        fees_total=fees_total,
        fees=fees_out,
        order_total=order_total,
    )


@router.post("/checkout/confirm", response=CheckoutConfirmOut, auth=_auth)
def checkout_confirm(request, payload: CheckoutConfirmIn):
    user = _require_user(request)

    addr = UserAddress.objects.filter(
        user=user, id=payload.shipping_address_id).first()
    if not addr:
        raise HttpError(404, "Shipping address not found")

    shipping_method = (payload.shipping_method or "").strip() or "lpexpress"
    pickup_point_id = _maybe_fill_pickup_point_id_from_user(
        user=user,
        shipping_method=shipping_method,
        pickup_point_id=getattr(payload, "pickup_point_id", None),
    )

    neopay_bank_bic = (getattr(payload, "neopay_bank_bic", None) or "").strip() or None

    payment_method = (payload.payment_method or "").strip() or "klix"
    if payment_method not in ["klix", "bank_transfer", "neopay"]:
        raise HttpError(400, "Unsupported payment_method")

    idem_key = (request.headers.get("Idempotency-Key") or "").strip()

    channel = (getattr(payload, "channel", "normal") or "normal").strip().lower()
    if channel not in {"normal", "outlet"}:
        raise HttpError(400, "Invalid channel")
    coupon_code = (getattr(payload, "coupon_code", None) or "").strip().lower() or None

    if idem_key:
        existing = Order.objects.filter(
            user=user, idempotency_key=idem_key).first()
        if existing:
            pi = getattr(existing, "payment_intent", None)
            return CheckoutConfirmOut(
                order_id=existing.id,
                payment_provider=(pi.provider if pi else "klix"),
                payment_status=(pi.status if pi else "pending"),
                redirect_url=(pi.redirect_url if pi else ""),
                payment_instructions=(
                    _bank_transfer_instructions_for(order_id=existing.id, country_code=existing.country_code)
                    if (pi and pi.provider == PaymentIntent.Provider.BANK_TRANSFER)
                    else ""
                ),
            )

    # Order-level consents are required at purchase time.
    consents_by_kind: dict[str, str] = {}
    for c in (payload.consents or []):
        kind = (c.kind or "").strip().lower()
        ver = (c.document_version or "").strip()
        if not kind or not ver:
            raise HttpError(400, "Invalid consent entry")
        consents_by_kind[kind] = ver

    missing = []
    if "terms" not in consents_by_kind:
        missing.append("terms")
    if "privacy" not in consents_by_kind:
        missing.append("privacy")
    if missing:
        raise HttpError(
            400, f"Missing required consents: {', '.join(missing)}")

    # Optional safety: if the front-end has stale versions, fail with 409 so it can refresh.
    current_terms = getattr(settings, "CHECKOUT_TERMS_VERSION", "v1")
    current_privacy = getattr(settings, "CHECKOUT_PRIVACY_VERSION", "v1")
    if consents_by_kind["terms"] != current_terms or consents_by_kind["privacy"] != current_privacy:
        raise HttpError(
            409, "Consent versions are outdated; refresh /checkout/consents")

    # Reuse preview logic for totals + validation
    preview = checkout_preview(
        request,
        CheckoutPreviewIn(
            shipping_address_id=payload.shipping_address_id,
            shipping_method=shipping_method,
            pickup_point_id=pickup_point_id,
            payment_method=payment_method,
            channel=channel,
            coupon_code=coupon_code,
        ),
    )

    cart = _get_cart_for_request(request, create=False)
    if cart is None:
        raise HttpError(400, "Cart is empty")
    items = list(
        CartItem.objects.select_related(
            "variant", "variant__product", "variant__product__tax_class", "offer")
        .filter(cart=cart)
        .order_by("id")
    )

    country_code = preview.country_code

    carrier_code, pickup_locker, pickup_snapshot = _validate_and_resolve_pickup(
        shipping_method=shipping_method,
        pickup_point_id=pickup_point_id,
        country_code=country_code,
    )

    ip_address = request.META.get("REMOTE_ADDR")
    user_agent = (request.META.get("HTTP_USER_AGENT") or "").strip()

    with transaction.atomic():
        dw = getattr(preview, "delivery_window", None)
        dw_min = None
        dw_max = None
        dw_kind = ""
        dw_rule = ""
        dw_source = ""
        try:
            raw_min = _dw_value(dw, "min_date")
            raw_max = _dw_value(dw, "max_date")
            if isinstance(raw_min, date):
                dw_min = raw_min
            elif raw_min:
                dw_min = date.fromisoformat(str(raw_min))
            if isinstance(raw_max, date):
                dw_max = raw_max
            elif raw_max:
                dw_max = date.fromisoformat(str(raw_max))
            dw_kind = str(_dw_value(dw, "kind") or "")
            dw_rule = str(_dw_value(dw, "rule_code") or "")
            dw_source = str(_dw_value(dw, "source") or "")
        except Exception:
            dw_min = None
            dw_max = None
            dw_kind = ""
            dw_rule = ""
            dw_source = ""

        order = Order.objects.create(
            user=user,
            status=Order.Status.PENDING_PAYMENT,
            idempotency_key=idem_key or "",
            currency="EUR",
            country_code=country_code,
            shipping_method=shipping_method,
            delivery_min_date=dw_min,
            delivery_max_date=dw_max,
            delivery_eta_kind=dw_kind,
            delivery_eta_rule_code=dw_rule,
            delivery_eta_source=dw_source,
            carrier_code=carrier_code,
            pickup_locker=pickup_locker,
            pickup_point_id=(
                pickup_locker.locker_id
                if pickup_locker
                else (pickup_snapshot.get("pickup_point_id") if pickup_snapshot else "")
            )
            or "",
            pickup_point_name=(
                pickup_locker.name
                if pickup_locker
                else (pickup_snapshot.get("pickup_point_name") if pickup_snapshot else "")
            )
            or "",
            pickup_point_raw=(
                pickup_locker.raw
                if pickup_locker
                else (pickup_snapshot.get("pickup_point_raw") if pickup_snapshot else {})
            )
            or {},
            items_net=preview.items_total.net,
            items_vat=preview.items_total.vat,
            items_gross=preview.items_total.gross,
            shipping_net=preview.shipping_total.net,
            shipping_vat=preview.shipping_total.vat,
            shipping_gross=preview.shipping_total.gross,
            total_net=preview.order_total.net,
            total_vat=preview.order_total.vat,
            total_gross=preview.order_total.gross,
            shipping_full_name=addr.full_name,
            shipping_company=addr.company,
            shipping_line1=addr.line1,
            shipping_city=addr.city,
            shipping_postal_code=addr.postal_code,
            shipping_country_code=addr.country_code,
            shipping_phone=addr.phone,
        )

        if coupon_code:
            coupon = Coupon.objects.filter(code=coupon_code).first()
            OrderDiscount.objects.create(
                order=order,
                kind=OrderDiscount.Kind.COUPON,
                code=coupon_code,
                name=(coupon.name if coupon else ""),
                net=preview.discount_total.net,
                vat=preview.discount_total.vat,
                gross=preview.discount_total.gross,
            )

            from promotions.services import reserve_coupon_for_order

            # Reserve coupon usage immediately on order creation so usage limits apply
            # even before payment is completed (important for bank transfer flows).
            if not reserve_coupon_for_order(order_id=order.id):
                raise HttpError(400, "Coupon usage limit reached")

            if coupon and coupon.is_valid_now() and coupon.is_free_shipping_for(shipping_method=shipping_method):
                order.shipping_net_manual = Decimal("0.00")
                order.save(update_fields=["shipping_net_manual"])

        lines: list[OrderLine] = []
        for it in items:
            v = it.variant
            primary = user.get_primary_customer_group() if user else None
            customer_group_id = int(primary.id) if primary else None
            unit_price, line_total, _compare_at, _disc_pct, vat_rate = _cart_item_money(
                item=it,
                country_code=country_code,
                channel=channel,
                customer_group_id=customer_group_id,
            )

            lines.append(
                OrderLine(
                    order=order,
                    variant=v,
                    offer=(it.offer if getattr(it, "offer_id", None) else None),
                    sku=v.sku,
                    name=(v.product.name if v.product_id else v.sku),
                    unit_net=unit_price.net,
                    vat_rate=vat_rate,
                    unit_vat=unit_price.vat,
                    unit_gross=unit_price.gross,
                    qty=it.qty,
                    total_net=line_total.net,
                    total_vat=line_total.vat,
                    total_gross=line_total.gross,
                )
            )

        OrderLine.objects.bulk_create(lines)

        try:
            reserve_inventory_for_order(order_id=order.id)
        except ValueError as exc:
            msg = str(exc) or "Not enough stock"
            if "not enough" in msg.lower():
                raise HttpError(409, "Not enough stock")
            raise HttpError(409, msg)

        OrderConsent.objects.bulk_create(
            [
                OrderConsent(
                    order=order,
                    kind=OrderConsent.Kind.TERMS,
                    document_version=consents_by_kind["terms"],
                    ip_address=ip_address,
                    user_agent=user_agent,
                ),
                OrderConsent(
                    order=order,
                    kind=OrderConsent.Kind.PRIVACY,
                    document_version=consents_by_kind["privacy"],
                    ip_address=ip_address,
                    user_agent=user_agent,
                ),
            ]
        )

        if preview.fees:
            fee_rows: list[OrderFee] = []
            for f in preview.fees:
                amt = f.amount
                fee_rows.append(
                    OrderFee(
                        order=order,
                        code=f.code,
                        name=f.name,
                        net=amt.net,
                        vat_rate=amt.vat_rate,
                        vat=amt.vat,
                        gross=amt.gross,
                    )
                )
            OrderFee.objects.bulk_create(fee_rows)

        order.recalculate_totals()
        order.save(update_fields=[
            "items_net",
            "items_vat",
            "items_gross",
            "shipping_net",
            "shipping_vat",
            "shipping_gross",
            "total_net",
            "total_vat",
            "total_gross",
        ])

        provider = (
            PaymentIntent.Provider.BANK_TRANSFER
            if payment_method == "bank_transfer"
            else (PaymentIntent.Provider.NEOPAY if payment_method == "neopay" else PaymentIntent.Provider.KLIX)
        )
        pi = PaymentIntent.objects.create(
            order=order,
            provider=provider,
            status=PaymentIntent.Status.PENDING,
            currency="EUR",
            amount_gross=order.total_gross,
            raw_request={
                "provider": provider,
                "created_at": timezone.now().isoformat(),
            },
        )

        if provider == PaymentIntent.Provider.NEOPAY:
            from payments.services.neopay import build_neopay_payment_link

            tx_id = f"order-{order.id}"
            link, neopay_payload = build_neopay_payment_link(
                amount=order.total_gross,
                currency=order.currency,
                transaction_id=tx_id,
                payment_purpose=f"Order {order.id}",
                bank_bic=neopay_bank_bic,
            )
            pi.external_id = tx_id
            pi.redirect_url = link
            pi.raw_request = {
                **(pi.raw_request or {}),
                "neopay": neopay_payload,
                "neopay_bank_bic_requested": (neopay_bank_bic or ""),
            }
            pi.save(update_fields=["external_id", "redirect_url", "raw_request", "updated_at"])

        # MVP: Klix redirect_url is empty until we plug in Klix API.

        # Clear cart after creating the order.
        CartItem.objects.filter(cart=cart).delete()

        bank_transfer_instructions = _bank_transfer_instructions_for(
            order_id=order.id, country_code=order.country_code
        )

    try:
        track_event(
            request=request,
            name="purchase",
            object_type="order",
            object_id=int(order.id),
            payload={
                "currency": str(order.currency or "EUR"),
                "value": str(order.total_gross),
                "country_code": str(order.country_code or ""),
                "shipping_method": str(order.shipping_method or ""),
            },
            country_code=str(order.country_code or ""),
            channel=str(channel or ""),
            outbox_providers=["newsman"],
        )
    except Exception:
        pass

    return CheckoutConfirmOut(
        order_id=order.id,
        payment_provider=pi.provider,
        payment_status=pi.status,
        redirect_url=pi.redirect_url or "",
        payment_instructions=(
            bank_transfer_instructions
            if pi.provider == PaymentIntent.Provider.BANK_TRANSFER
            else ""
        ),
    )


@router.get("/orders", response=list[OrderOut], auth=_auth)
def list_orders(request, limit: int = 20):
    user = _require_user(request)
    limit = max(1, min(int(limit or 20), 50))

    orders = (
        Order.objects.filter(user=user)
        .select_related("payment_intent")
        .prefetch_related("lines", "fees", "discounts")
        .order_by("-created_at")[:limit]
    )

    result: list[OrderOut] = []
    for o in orders:
        pi = getattr(o, "payment_intent", None)
        payment_instructions = ""
        if pi and pi.provider == PaymentIntent.Provider.BANK_TRANSFER:
            payment_instructions = _bank_transfer_instructions_for(order_id=o.id, country_code=o.country_code)

        status_label = ""
        delivery_status_label = ""
        fulfillment_mode_label = ""
        supplier_reservation_status_label = ""
        try:
            status_label = str(getattr(o, "get_status_display")())
        except Exception:
            status_label = ""
        try:
            delivery_status_label = str(getattr(o, "get_delivery_status_display")())
        except Exception:
            delivery_status_label = ""
        try:
            fulfillment_mode_label = str(getattr(o, "get_fulfillment_mode_display")())
        except Exception:
            fulfillment_mode_label = ""
        try:
            supplier_reservation_status_label = str(getattr(o, "get_supplier_reservation_status_display")())
        except Exception:
            supplier_reservation_status_label = ""

        payment_provider_label = ""
        payment_status_label = ""
        if pi:
            try:
                payment_provider_label = str(PaymentIntent.Provider(pi.provider).label)
            except Exception:
                payment_provider_label = ""
            try:
                payment_status_label = str(PaymentIntent.Status(pi.status).label)
            except Exception:
                payment_status_label = ""

        fees_out: list[FeeOut] = []
        fees_net = Decimal("0.00")
        fees_vat = Decimal("0.00")
        fees_gross = Decimal("0.00")
        for f in o.fees.all():
            fees_net += Decimal(f.net)
            fees_vat += Decimal(f.vat)
            fees_gross += Decimal(f.gross)
            fees_out.append(
                FeeOut(
                    code=f.code,
                    name=f.name,
                    amount=MoneyOut(
                        currency=o.currency,
                        net=f.net,
                        vat_rate=f.vat_rate,
                        vat=f.vat,
                        gross=f.gross,
                    ),
                )
            )

        lines_out: list[OrderLineOut] = []
        agg_min = None
        agg_max = None
        agg_meta: dict | None = None
        for ln in o.lines.all():
            unit = MoneyOut(currency=o.currency, net=ln.unit_net,
                            vat_rate=ln.vat_rate, vat=ln.unit_vat, gross=ln.unit_gross)
            total = MoneyOut(currency=o.currency, net=ln.total_net,
                             vat_rate=ln.vat_rate, vat=ln.total_vat, gross=ln.total_gross)

            compare_at_unit = None
            compare_at_total = None
            disc_pct = None
            try:
                list_unit_net = Decimal(getattr(getattr(ln, "variant", None), "price_eur", 0) or 0)
                base_unit_net = (
                    _effective_offer_unit_net(list_unit_net=list_unit_net, offer=ln.offer)
                    if getattr(ln, "offer_id", None)
                    else list_unit_net
                )
                disc_pct = _discount_percent(list_unit_net=base_unit_net, sale_unit_net=ln.unit_net)
                if disc_pct is not None:
                    base_u = money_from_net(currency=o.currency, unit_net=base_unit_net, vat_rate=ln.vat_rate, qty=1)
                    base_t = money_from_net(currency=o.currency, unit_net=base_unit_net, vat_rate=ln.vat_rate, qty=int(ln.qty))
                    compare_at_unit = MoneyOut(
                        currency=base_u.currency,
                        net=base_u.net,
                        vat_rate=base_u.vat_rate,
                        vat=base_u.vat,
                        gross=base_u.gross,
                    )
                    compare_at_total = MoneyOut(
                        currency=base_t.currency,
                        net=base_t.net,
                        vat_rate=base_t.vat_rate,
                        vat=base_t.vat,
                        gross=base_t.gross,
                    )
            except Exception:
                pass

            lines_out.append(
                OrderLineOut(
                    id=ln.id,
                    sku=ln.sku,
                    name=ln.name,
                    qty=ln.qty,
                    unit_price=unit,
                    compare_at_unit_price=compare_at_unit,
                    discount_percent=disc_pct,
                    line_total=total,
                    compare_at_line_total=compare_at_total,
                    delivery_window=None,
                )
            )

            # Per-line delivery window
            dw = None
            try:
                v = getattr(ln, "variant", None)
                p = getattr(v, "product", None) if v is not None else None
                line_channel = (
                    "outlet"
                    if getattr(ln, "offer_id", None)
                    and ln.offer
                    and getattr(ln.offer, "offer_visibility", None) == InventoryItem.OfferVisibility.OUTLET
                    else "normal"
                )
                dw = estimate_delivery_window(
                    now=timezone.now(),
                    country_code=o.country_code,
                    channel=line_channel,
                    warehouse_id=int(ln.offer.warehouse_id) if getattr(ln, "offer_id", None) and ln.offer and ln.offer.warehouse_id else None,
                    product_id=int(p.id) if p else None,
                    brand_id=int(p.brand_id) if p and p.brand_id else None,
                    category_id=int(p.category_id) if p and p.category_id else None,
                    product_group_id=int(getattr(p, "group_id", None)) if p and getattr(p, "group_id", None) else None,
                )
            except Exception:
                dw = None

            if dw is not None:
                dw_out = {
                    "min_date": dw.min_date.isoformat(),
                    "max_date": dw.max_date.isoformat(),
                    "kind": dw.kind,
                    "rule_code": dw.rule_code,
                    "source": dw.source,
                }
                lines_out[-1].delivery_window = dw_out
                if agg_min is None or dw.min_date > agg_min:
                    agg_min = dw.min_date
                if agg_max is None or dw.max_date > agg_max:
                    agg_max = dw.max_date
                agg_meta = dw_out

        items_total = MoneyOut(currency=o.currency, net=o.items_net, vat_rate=Decimal(
            "0"), vat=o.items_vat, gross=o.items_gross)
        shipping_total = MoneyOut(currency=o.currency, net=o.shipping_net, vat_rate=Decimal(
            "0"), vat=o.shipping_vat, gross=o.shipping_gross)
        fees_total = MoneyOut(currency=o.currency, net=fees_net, vat_rate=Decimal(
            "0"), vat=fees_vat, gross=fees_gross)

        disc_net = Decimal("0.00")
        disc_vat = Decimal("0.00")
        disc_gross = Decimal("0.00")
        for d in o.discounts.all():
            disc_net += Decimal(d.net)
            disc_vat += Decimal(d.vat)
            disc_gross += Decimal(d.gross)
        discount_total = MoneyOut(currency=o.currency, net=disc_net, vat_rate=Decimal("0"), vat=disc_vat, gross=disc_gross)

        order_total = MoneyOut(currency=o.currency, net=o.total_net, vat_rate=Decimal(
            "0"), vat=o.total_vat, gross=o.total_gross)

        delivery_window = None
        if getattr(o, "delivery_min_date", None) and getattr(o, "delivery_max_date", None):
            delivery_window = {
                "min_date": o.delivery_min_date.isoformat(),
                "max_date": o.delivery_max_date.isoformat(),
                "kind": str(getattr(o, "delivery_eta_kind", "") or "estimated"),
                "rule_code": str(getattr(o, "delivery_eta_rule_code", "") or ""),
                "source": str(getattr(o, "delivery_eta_source", "") or ""),
            }
        elif agg_min is not None and agg_max is not None:
            # Backward-compatible fallback if snapshot was not stored (older orders).
            delivery_window = {
                "min_date": agg_min.isoformat(),
                "max_date": agg_max.isoformat(),
                "kind": (str((agg_meta or {}).get("kind") or "estimated")),
                "rule_code": str((agg_meta or {}).get("rule_code") or ""),
                "source": str((agg_meta or {}).get("source") or ""),
            }

        result.append(
            OrderOut(
                id=o.id,
                status=o.status,
                status_label=status_label,
                delivery_status=o.delivery_status,
                delivery_status_label=delivery_status_label,
                fulfillment_mode=(getattr(o, "fulfillment_mode", "") or ""),
                fulfillment_mode_label=fulfillment_mode_label,
                supplier_reservation_status=(getattr(o, "supplier_reservation_status", "") or ""),
                supplier_reservation_status_label=supplier_reservation_status_label,
                supplier_reserved_at=(o.supplier_reserved_at.isoformat() if getattr(o, "supplier_reserved_at", None) else ""),
                supplier_reference=(getattr(o, "supplier_reference", "") or ""),
                currency=o.currency,
                country_code=o.country_code,
                shipping_method=o.shipping_method,
                carrier_code=o.carrier_code,
                tracking_number=o.tracking_number,
                payment_provider=(pi.provider if pi else ""),
                payment_provider_label=payment_provider_label,
                payment_status=(pi.status if pi else ""),
                payment_status_label=payment_status_label,
                payment_redirect_url=(pi.redirect_url if pi else ""),
                payment_instructions=payment_instructions,
                neopay_bank_bic=(pi.neopay_bank_bic if pi else ""),
                neopay_bank_name=(pi.neopay_bank_name if pi else ""),
                items=lines_out,
                delivery_window=delivery_window,
                items_total=items_total,
                discount_total=discount_total,
                shipping_total=shipping_total,
                fees_total=fees_total,
                fees=fees_out,
                order_total=order_total,
                created_at=o.created_at.isoformat(),
            )
        )

    return result


@router.get("/orders/{order_id}", response=OrderOut, auth=_auth)
def get_order(request, order_id: int):
    user = _require_user(request)

    o = (
        Order.objects.filter(user=user, id=order_id)
        .select_related("payment_intent")
        .prefetch_related("lines", "fees", "discounts")
        .first()
    )
    if not o:
        raise HttpError(404, "Order not found")

    lines_out: list[OrderLineOut] = []
    agg_min = None
    agg_max = None
    agg_meta: dict | None = None
    for ln in o.lines.all():
        unit = MoneyOut(currency=o.currency, net=ln.unit_net,
                        vat_rate=ln.vat_rate, vat=ln.unit_vat, gross=ln.unit_gross)
        total = MoneyOut(currency=o.currency, net=ln.total_net,
                         vat_rate=ln.vat_rate, vat=ln.total_vat, gross=ln.total_gross)
        lines_out.append(OrderLineOut(id=ln.id, sku=ln.sku, name=ln.name,
                         qty=ln.qty, unit_price=unit, line_total=total, delivery_window=None))

        dw = None
        try:
            v = getattr(ln, "variant", None)
            p = getattr(v, "product", None) if v is not None else None
            line_channel = (
                "outlet"
                if getattr(ln, "offer_id", None)
                and ln.offer
                and getattr(ln.offer, "offer_visibility", None) == InventoryItem.OfferVisibility.OUTLET
                else "normal"
            )
            dw = estimate_delivery_window(
                now=timezone.now(),
                country_code=o.country_code,
                channel=line_channel,
                warehouse_id=int(ln.offer.warehouse_id) if getattr(ln, "offer_id", None) and ln.offer and ln.offer.warehouse_id else None,
                product_id=int(p.id) if p else None,
                brand_id=int(p.brand_id) if p and p.brand_id else None,
                category_id=int(p.category_id) if p and p.category_id else None,
                product_group_id=int(getattr(p, "group_id", None)) if p and getattr(p, "group_id", None) else None,
            )
        except Exception:
            dw = None

        if dw is not None:
            dw_out = {
                "min_date": dw.min_date.isoformat(),
                "max_date": dw.max_date.isoformat(),
                "kind": dw.kind,
                "rule_code": dw.rule_code,
                "source": dw.source,
            }
            lines_out[-1].delivery_window = dw_out
            if agg_min is None or dw.min_date > agg_min:
                agg_min = dw.min_date
            if agg_max is None or dw.max_date > agg_max:
                agg_max = dw.max_date
            agg_meta = dw_out

    items_total = MoneyOut(currency=o.currency, net=o.items_net, vat_rate=Decimal(
        "0"), vat=o.items_vat, gross=o.items_gross)
    shipping_total = MoneyOut(currency=o.currency, net=o.shipping_net, vat_rate=Decimal(
        "0"), vat=o.shipping_vat, gross=o.shipping_gross)

    fees_out: list[FeeOut] = []
    fees_net = Decimal("0.00")
    fees_vat = Decimal("0.00")
    fees_gross = Decimal("0.00")
    for f in o.fees.all():
        fees_net += Decimal(f.net)
        fees_vat += Decimal(f.vat)
        fees_gross += Decimal(f.gross)
        fees_out.append(
            FeeOut(
                code=f.code,
                name=f.name,
                amount=MoneyOut(
                    currency=o.currency,
                    net=f.net,
                    vat_rate=f.vat_rate,
                    vat=f.vat,
                    gross=f.gross,
                ),
            )
        )

    fees_total = MoneyOut(currency=o.currency, net=fees_net, vat_rate=Decimal(
        "0"), vat=fees_vat, gross=fees_gross)

    disc_net = Decimal("0.00")
    disc_vat = Decimal("0.00")
    disc_gross = Decimal("0.00")
    for d in o.discounts.all():
        disc_net += Decimal(d.net)
        disc_vat += Decimal(d.vat)
        disc_gross += Decimal(d.gross)
    discount_total = MoneyOut(currency=o.currency, net=disc_net, vat_rate=Decimal("0"), vat=disc_vat, gross=disc_gross)

    order_total = MoneyOut(currency=o.currency, net=o.total_net, vat_rate=Decimal(
        "0"), vat=o.total_vat, gross=o.total_gross)

    pi = getattr(o, "payment_intent", None)
    payment_instructions = ""
    if pi and pi.provider == PaymentIntent.Provider.BANK_TRANSFER:
        payment_instructions = _bank_transfer_instructions_for(order_id=o.id, country_code=o.country_code)

    status_label = ""
    delivery_status_label = ""
    fulfillment_mode_label = ""
    supplier_reservation_status_label = ""
    try:
        status_label = str(getattr(o, "get_status_display")())
    except Exception:
        status_label = ""
    try:
        delivery_status_label = str(getattr(o, "get_delivery_status_display")())
    except Exception:
        delivery_status_label = ""
    try:
        fulfillment_mode_label = str(getattr(o, "get_fulfillment_mode_display")())
    except Exception:
        fulfillment_mode_label = ""
    try:
        supplier_reservation_status_label = str(getattr(o, "get_supplier_reservation_status_display")())
    except Exception:
        supplier_reservation_status_label = ""

    payment_provider_label = ""
    payment_status_label = ""
    if pi:
        try:
            payment_provider_label = str(PaymentIntent.Provider(pi.provider).label)
        except Exception:
            payment_provider_label = ""
        try:
            payment_status_label = str(PaymentIntent.Status(pi.status).label)
        except Exception:
            payment_status_label = ""

    delivery_window = None
    if getattr(o, "delivery_min_date", None) and getattr(o, "delivery_max_date", None):
        delivery_window = {
            "min_date": o.delivery_min_date.isoformat(),
            "max_date": o.delivery_max_date.isoformat(),
            "kind": str(getattr(o, "delivery_eta_kind", "") or "estimated"),
            "rule_code": str(getattr(o, "delivery_eta_rule_code", "") or ""),
            "source": str(getattr(o, "delivery_eta_source", "") or ""),
        }
    elif agg_min is not None and agg_max is not None:
        delivery_window = {
            "min_date": agg_min.isoformat(),
            "max_date": agg_max.isoformat(),
            "kind": (str((agg_meta or {}).get("kind") or "estimated")),
            "rule_code": str((agg_meta or {}).get("rule_code") or ""),
            "source": str((agg_meta or {}).get("source") or ""),
        }

    return OrderOut(
        id=o.id,
        status=o.status,
        status_label=status_label,
        delivery_status=o.delivery_status,
        delivery_status_label=delivery_status_label,
        fulfillment_mode=(getattr(o, "fulfillment_mode", "") or ""),
        fulfillment_mode_label=fulfillment_mode_label,
        supplier_reservation_status=(getattr(o, "supplier_reservation_status", "") or ""),
        supplier_reservation_status_label=supplier_reservation_status_label,
        supplier_reserved_at=(o.supplier_reserved_at.isoformat() if getattr(o, "supplier_reserved_at", None) else ""),
        supplier_reference=(getattr(o, "supplier_reference", "") or ""),
        currency=o.currency,
        country_code=o.country_code,
        shipping_method=o.shipping_method,
        carrier_code=o.carrier_code,
        tracking_number=o.tracking_number,
        payment_provider=(pi.provider if pi else ""),
        payment_provider_label=payment_provider_label,
        payment_status=(pi.status if pi else ""),
        payment_status_label=payment_status_label,
        payment_redirect_url=(pi.redirect_url if pi else ""),
        payment_instructions=payment_instructions,
        neopay_bank_bic=(pi.neopay_bank_bic if pi else ""),
        neopay_bank_name=(pi.neopay_bank_name if pi else ""),
        items=lines_out,
        delivery_window=delivery_window,
        items_total=items_total,
        discount_total=discount_total,
        shipping_total=shipping_total,
        fees_total=fees_total,
        fees=fees_out,
        order_total=order_total,
        created_at=o.created_at.isoformat(),
    )
