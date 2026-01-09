from __future__ import annotations

from decimal import Decimal

from django.conf import settings
from django.db import models
from django.db import transaction
from django.utils import timezone
from ninja import Router
from ninja.errors import HttpError

from accounts.auth import JWTAuth
from accounts.models import UserAddress
from catalog.models import Variant
from pricing.services import get_vat_rate

from .models import Cart, CartItem, Order, OrderConsent, OrderFee, OrderLine, PaymentIntent
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
    calculate_fees,
    get_shipping_net,
    inventory_available_for_variant,
    money_from_net,
    get_shipping_tax_class,
    reserve_inventory_for_order,
)


router = Router(tags=["checkout"])
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


def _user_from_bearer_if_present(request):
    # Cart endpoints must work without auth; but if Bearer token is present, use it.
    try:
        auth = request.headers.get("Authorization") or ""
    except Exception:
        auth = ""

    auth = (auth or "").strip()
    if not auth.lower().startswith("bearer "):
        # Also allow normal Django session auth if present
        try:
            u = getattr(request, "user", None)
            if u is not None and getattr(u, "is_authenticated", False):
                return u
        except Exception:
            return None
        return None

    token = auth.split(" ", 1)[1].strip()
    if not token:
        return None

    try:
        from accounts.jwt_utils import decode_token
        from django.contrib.auth import get_user_model

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
    user = _user_from_bearer_if_present(request)

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
                for it in CartItem.objects.filter(cart=guest_cart).select_related("variant"):
                    existing = CartItem.objects.filter(
                        cart=user_cart, variant=it.variant).first()
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
    if carrier == "dpd":
        from dpd.models import DpdLocker

        locker = DpdLocker.objects.filter(
            locker_id=pickup_point_id, is_active=True).first()
        if not locker:
            raise HttpError(400, "Invalid pickup_point_id")

        if locker.country_code and country_code and locker.country_code.upper() != country_code:
            raise HttpError(400, "pickup_point_id country mismatch")

        return carrier_code, locker, None

    if carrier == "lpexpress":
        from unisend.models import UnisendTerminal

        terminal = UnisendTerminal.objects.filter(
            terminal_id=pickup_point_id, is_active=True).first()
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
        return carrier_code, None, snapshot

    raise HttpError(400, "pickup_point_id is not supported for this carrier")


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


def _serialize_cart_items(*, items: list[CartItem], country_code: str) -> tuple[list[CartItemOut], MoneyOut]:
    out_items: list[CartItemOut] = []

    total_net = Decimal("0.00")
    total_vat = Decimal("0.00")
    total_gross = Decimal("0.00")

    for it in items:
        v = it.variant
        unit_price, line_total, _vat_rate = _variant_money(
            variant=v, country_code=country_code, qty=int(it.qty))

        stock_available = inventory_available_for_variant(variant_id=v.id)

        total_net += line_total.net
        total_vat += line_total.vat
        total_gross += line_total.gross

        out_items.append(
            CartItemOut(
                id=it.id,
                variant_id=v.id,
                sku=v.sku,
                name=(v.product.name if v.product_id else v.sku),
                qty=it.qty,
                stock_available=int(stock_available),
                unit_price=unit_price,
                line_total=line_total,
            )
        )

    items_total = MoneyOut(
        currency="EUR",
        net=total_net,
        vat_rate=Decimal("0"),
        vat=total_vat,
        gross=total_gross,
    )
    return out_items, items_total


@router.get("/cart", response=CartOut)
def get_cart(request, country_code: str = "LT"):
    country_code = (country_code or "").strip().upper()
    if len(country_code) != 2:
        raise HttpError(400, "Invalid country_code")

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
            "variant", "variant__product", "variant__product__tax_class")
        .filter(cart=cart)
        .order_by("id")
    )

    out_items, items_total = _serialize_cart_items(
        items=items, country_code=country_code)
    return CartOut(country_code=country_code, items=out_items, items_total=items_total)


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

    cart = _get_cart_for_request(request, create=True)
    if cart is None:
        raise HttpError(400, "Session is not available")

    with transaction.atomic():
        item = CartItem.objects.filter(cart=cart, variant=variant).first()
        if item:
            item.qty = item.qty + qty
            item.save(update_fields=["qty", "updated_at"])
        else:
            CartItem.objects.create(cart=cart, variant=variant, qty=qty)

    return get_cart(request, country_code=country_code)


@router.patch("/cart/items/{item_id}", response=CartOut)
def update_cart_item(request, item_id: int, payload: CartItemUpdateIn, country_code: str = "LT"):
    qty = int(payload.qty or 0)

    cart = _get_cart_for_request(request, create=False)
    if cart is None:
        raise HttpError(404, "Cart item not found")
    item = (
        CartItem.objects.select_related(
            "variant", "variant__product", "variant__product__tax_class")
        .filter(cart=cart, id=item_id)
        .first()
    )
    if not item:
        raise HttpError(404, "Cart item not found")

    with transaction.atomic():
        if qty <= 0:
            item.delete()
        else:
            item.qty = qty
            item.save(update_fields=["qty", "updated_at"])

    return get_cart(request, country_code=country_code)


@router.delete("/cart/items/{item_id}", response=CartOut)
def delete_cart_item(request, item_id: int, country_code: str = "LT"):
    cart = _get_cart_for_request(request, create=False)
    if cart is None:
        raise HttpError(404, "Cart item not found")

    deleted = CartItem.objects.filter(cart=cart, id=item_id).delete()[0]
    if not deleted:
        raise HttpError(404, "Cart item not found")

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

    country_code = _country_code_from_address(addr)

    # Validate pickup-point selection early (even though we don't persist anything on preview).
    _validate_and_resolve_pickup(
        shipping_method=shipping_method,
        pickup_point_id=getattr(payload, "pickup_point_id", None),
        country_code=country_code,
    )

    cart = _get_cart_for_request(request, create=False)
    if cart is None:
        raise HttpError(400, "Cart is empty")
    items = list(
        CartItem.objects.select_related(
            "variant", "variant__product", "variant__product__tax_class")
        .filter(cart=cart)
        .order_by("id")
    )
    if not items:
        raise HttpError(400, "Cart is empty")

    # Stock check
    for it in items:
        available = inventory_available_for_variant(variant_id=it.variant_id)
        if int(available) < int(it.qty):
            raise HttpError(409, f"Not enough stock for {it.variant.sku}")

    out_items, items_total = _serialize_cart_items(
        items=items, country_code=country_code)

    try:
        shipping_net = get_shipping_net(
            shipping_method=shipping_method, country_code=country_code
        )
    except ValueError:
        raise HttpError(400, "Unsupported shipping_method")
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

    order_net = items_total.net + shipping_money.net + fees_net
    order_vat = items_total.vat + shipping_money.vat + fees_vat
    order_gross = items_total.gross + shipping_money.gross + fees_gross

    shipping_total = MoneyOut(
        currency="EUR",
        net=shipping_money.net,
        vat_rate=shipping_money.vat_rate,
        vat=shipping_money.vat,
        gross=shipping_money.gross,
    )
    fees_total = MoneyOut(currency="EUR", net=fees_net, vat_rate=Decimal("0"), vat=fees_vat, gross=fees_gross)
    order_total = MoneyOut(currency="EUR", net=order_net, vat_rate=Decimal(
        "0"), vat=order_vat, gross=order_gross)

    return CheckoutPreviewOut(
        country_code=country_code,
        shipping_method=shipping_method,
        items=out_items,
        items_total=items_total,
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
    pickup_point_id = (getattr(payload, "pickup_point_id",
                       None) or "").strip() or None

    neopay_bank_bic = (getattr(payload, "neopay_bank_bic", None) or "").strip() or None

    payment_method = (payload.payment_method or "").strip() or "klix"
    if payment_method not in ["klix", "bank_transfer", "neopay"]:
        raise HttpError(400, "Unsupported payment_method")

    idem_key = (request.headers.get("Idempotency-Key") or "").strip()

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
        ),
    )

    cart = _get_cart_for_request(request, create=False)
    if cart is None:
        raise HttpError(400, "Cart is empty")
    items = list(
        CartItem.objects.select_related(
            "variant", "variant__product", "variant__product__tax_class")
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
        order = Order.objects.create(
            user=user,
            status=Order.Status.PENDING_PAYMENT,
            idempotency_key=idem_key or "",
            currency="EUR",
            country_code=country_code,
            shipping_method=shipping_method,
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

        lines: list[OrderLine] = []
        for it in items:
            v = it.variant
            unit_price, line_total, vat_rate = _variant_money(
                variant=v, country_code=country_code, qty=int(it.qty))

            lines.append(
                OrderLine(
                    order=order,
                    variant=v,
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
        .prefetch_related("lines", "fees")
        .order_by("-created_at")[:limit]
    )

    result: list[OrderOut] = []
    for o in orders:
        pi = getattr(o, "payment_intent", None)
        payment_instructions = ""
        if pi and pi.provider == PaymentIntent.Provider.BANK_TRANSFER:
            payment_instructions = _bank_transfer_instructions_for(order_id=o.id, country_code=o.country_code)

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
        for ln in o.lines.all():
            unit = MoneyOut(currency=o.currency, net=ln.unit_net,
                            vat_rate=ln.vat_rate, vat=ln.unit_vat, gross=ln.unit_gross)
            total = MoneyOut(currency=o.currency, net=ln.total_net,
                             vat_rate=ln.vat_rate, vat=ln.total_vat, gross=ln.total_gross)
            lines_out.append(OrderLineOut(
                id=ln.id, sku=ln.sku, name=ln.name, qty=ln.qty, unit_price=unit, line_total=total))

        items_total = MoneyOut(currency=o.currency, net=o.items_net, vat_rate=Decimal(
            "0"), vat=o.items_vat, gross=o.items_gross)
        shipping_total = MoneyOut(currency=o.currency, net=o.shipping_net, vat_rate=Decimal(
            "0"), vat=o.shipping_vat, gross=o.shipping_gross)
        fees_total = MoneyOut(currency=o.currency, net=fees_net, vat_rate=Decimal(
            "0"), vat=fees_vat, gross=fees_gross)
        order_total = MoneyOut(currency=o.currency, net=o.total_net, vat_rate=Decimal(
            "0"), vat=o.total_vat, gross=o.total_gross)

        result.append(
            OrderOut(
                id=o.id,
                status=o.status,
                delivery_status=o.delivery_status,
                fulfillment_mode=(getattr(o, "fulfillment_mode", "") or ""),
                supplier_reservation_status=(getattr(o, "supplier_reservation_status", "") or ""),
                supplier_reserved_at=(o.supplier_reserved_at.isoformat() if getattr(o, "supplier_reserved_at", None) else ""),
                supplier_reference=(getattr(o, "supplier_reference", "") or ""),
                currency=o.currency,
                country_code=o.country_code,
                shipping_method=o.shipping_method,
                carrier_code=o.carrier_code,
                tracking_number=o.tracking_number,
                payment_provider=(pi.provider if pi else ""),
                payment_status=(pi.status if pi else ""),
                payment_redirect_url=(pi.redirect_url if pi else ""),
                payment_instructions=payment_instructions,
                neopay_bank_bic=(pi.neopay_bank_bic if pi else ""),
                neopay_bank_name=(pi.neopay_bank_name if pi else ""),
                items=lines_out,
                items_total=items_total,
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
        .prefetch_related("lines", "fees")
        .first()
    )
    if not o:
        raise HttpError(404, "Order not found")

    lines_out: list[OrderLineOut] = []
    for ln in o.lines.all():
        unit = MoneyOut(currency=o.currency, net=ln.unit_net,
                        vat_rate=ln.vat_rate, vat=ln.unit_vat, gross=ln.unit_gross)
        total = MoneyOut(currency=o.currency, net=ln.total_net,
                         vat_rate=ln.vat_rate, vat=ln.total_vat, gross=ln.total_gross)
        lines_out.append(OrderLineOut(id=ln.id, sku=ln.sku, name=ln.name,
                         qty=ln.qty, unit_price=unit, line_total=total))

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
    order_total = MoneyOut(currency=o.currency, net=o.total_net, vat_rate=Decimal(
        "0"), vat=o.total_vat, gross=o.total_gross)

    pi = getattr(o, "payment_intent", None)
    payment_instructions = ""
    if pi and pi.provider == PaymentIntent.Provider.BANK_TRANSFER:
        payment_instructions = _bank_transfer_instructions_for(order_id=o.id, country_code=o.country_code)

    return OrderOut(
        id=o.id,
        status=o.status,
        delivery_status=o.delivery_status,
        fulfillment_mode=(getattr(o, "fulfillment_mode", "") or ""),
        supplier_reservation_status=(getattr(o, "supplier_reservation_status", "") or ""),
        supplier_reserved_at=(o.supplier_reserved_at.isoformat() if getattr(o, "supplier_reserved_at", None) else ""),
        supplier_reference=(getattr(o, "supplier_reference", "") or ""),
        currency=o.currency,
        country_code=o.country_code,
        shipping_method=o.shipping_method,
        carrier_code=o.carrier_code,
        tracking_number=o.tracking_number,
        payment_provider=(pi.provider if pi else ""),
        payment_status=(pi.status if pi else ""),
        payment_redirect_url=(pi.redirect_url if pi else ""),
        payment_instructions=payment_instructions,
        neopay_bank_bic=(pi.neopay_bank_bic if pi else ""),
        neopay_bank_name=(pi.neopay_bank_name if pi else ""),
        items=lines_out,
        items_total=items_total,
        shipping_total=shipping_total,
        fees_total=fees_total,
        fees=fees_out,
        order_total=order_total,
        created_at=o.created_at.isoformat(),
    )
