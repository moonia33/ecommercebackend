from __future__ import annotations

from decimal import Decimal
from datetime import date

import logging

from django.conf import settings
from django.db import models
from django.db import transaction
from django.utils import timezone
from ninja import Router
from ninja.errors import HttpError

from accounts.auth import JWTAuth
from accounts.models import UserAddress, UserPhone, UserPickupPoint
from catalog.models import Category, InventoryItem, Product, Variant
from pricing.services import get_vat_rate
from promotions.models import Coupon
from promotions.services import apply_promo_to_unit_net
from shipping.services import estimate_delivery_window

from analytics.services import track_event
from api.i18n import get_request_language_code
from api.shop_context import get_request_shop, shop_context_enabled, shop_context_strict

from checkout.models import Cart, CartItem, Order, OrderConsent, OrderDiscount, OrderFee, OrderLine, PaymentIntent
from catalog.localization import localize_option_type, localize_option_value
from catalog.services.image_out import image_out
from catalog.shop_pricing import resolve_shop_variant_price
from catalog.shop_scope import apply_shop_product_scope
from checkout.schemas import (
    CartItemAddIn,
    CartItemOptionOut,
    CartItemOut,
    CartItemUpdateIn,
    CartOut,
    ConsentDefinitionOut,
    CheckoutConfirmIn,
    CheckoutConfirmOut,
    CheckoutPreviewIn,
    CheckoutPreviewOut,
    ApplyPickupPointIn,
    ApplyPickupPointOut,
    MoneyOut,
    OrderOut,
    OrderLineOut,
    PaymentMethodOut,
    PaymentOptionOut,
    FeeOut,
    ShippingMethodOut,
)
from checkout.services import (
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

logger = logging.getLogger(__name__)

_auth = JWTAuth()


def _request_shop_id(request) -> int | None:
    shop = get_request_shop(request)
    return int(shop.id) if shop else None


def _ensure_shop_context_if_strict(request) -> None:
    if shop_context_enabled() and shop_context_strict() and get_request_shop(request) is None:
        raise HttpError(404, "Shop not resolved for host")


def _dw_value(dw, key: str):
    try:
        if dw is None:
            return None
        if hasattr(dw, "get"):
            return dw.get(key)
        return getattr(dw, key, None)
    except Exception:
        return None


def _primary_phone_for_user(user) -> str:
    p = UserPhone.objects.filter(user=user, is_primary=True).order_by("-created_at").first()
    return (p.phone if p else "") or ""


def _full_name_for_user(user) -> str:
    first = (getattr(user, "first_name", "") or "").strip()
    last = (getattr(user, "last_name", "") or "").strip()
    return (first + " " + last).strip()


def _pickup_point_address_fields(*, carrier_code: str, pickup_locker, pickup_snapshot: dict | None):
    carrier_code = (carrier_code or "").strip().lower()

    if pickup_locker is not None:
        line1 = str(getattr(pickup_locker, "street", "") or "").strip()
        city = str(getattr(pickup_locker, "city", "") or "").strip()
        postal_code = str(getattr(pickup_locker, "postal_code", "") or "").strip()
        country_code = str(getattr(pickup_locker, "country_code", "") or "").strip().upper()
        label_name = str(getattr(pickup_locker, "name", "") or "").strip()
        pickup_point_id = str(getattr(pickup_locker, "locker_id", "") or "").strip()
        return {
            "label_name": label_name,
            "pickup_point_id": pickup_point_id,
            "line1": line1,
            "city": city,
            "postal_code": postal_code,
            "country_code": country_code,
        }

    snap = pickup_snapshot or {}

    raw = snap.get("pickup_point_raw") if isinstance(snap, dict) else None
    raw = raw if isinstance(raw, dict) else {}

    line1 = (
        str(raw.get("address") or "").strip()
        or str(raw.get("street") or "").strip()
        or str(raw.get("line1") or "").strip()
    )
    city = (
        str(raw.get("city") or "").strip()
        or str(raw.get("locality") or "").strip()
        or str(raw.get("town") or "").strip()
    )
    postal_code = (
        str(raw.get("postalCode") or "").strip()
        or str(raw.get("postal_code") or "").strip()
        or str(raw.get("zip") or "").strip()
        or str(raw.get("zipCode") or "").strip()
    )
    country_code = (
        str(raw.get("countryCode") or "").strip()
        or str(raw.get("country_code") or "").strip()
        or str(raw.get("country") or "").strip()
    )

    return {
        "label_name": str(snap.get("pickup_point_name") or "").strip(),
        "pickup_point_id": str(snap.get("pickup_point_id") or "").strip(),
        "line1": line1,
        "city": city,
        "postal_code": postal_code,
        "country_code": str(country_code or "").strip().upper(),
    }


_PROVIDER_MAP = {
    "bank_transfer": PaymentIntent.Provider.BANK_TRANSFER,
    "montonio": PaymentIntent.Provider.MONTONIO,
    "cod": PaymentIntent.Provider.COD,
    "inbank": PaymentIntent.Provider.INBANK,
    "gift_card": PaymentIntent.Provider.GIFT_CARD,
}


def _resolve_checkout_payment_method(
    *, code: str, country_code: str, shipping_method: str = "",
):
    """Map a checkout `payment_method` code to a PaymentIntent provider.

    Returns (provider, PaymentMethod|None). Raises HttpError(400) when the
    code is unknown, not configured, or restricted by shipping method.
    """
    code = (code or "").strip().lower()
    if code not in _PROVIDER_MAP:
        raise HttpError(400, "Unsupported payment_method")
    provider = _PROVIDER_MAP[code]

    from payments.models import PaymentMethod

    pm = (
        PaymentMethod.objects.filter(is_active=True, code=code)
        .filter(
            models.Q(country_code="")
            | models.Q(country_code=(country_code or "").strip().upper())
        )
        .prefetch_related("allowed_shipping_methods")
        .order_by("-country_code")
        .first()
    )
    if pm is None:
        # No configured row — only the built-in methods stay available.
        if code not in {"bank_transfer", "montonio"}:
            raise HttpError(400, "Payment method not available")
        return provider, None

    allowed = {m.code for m in pm.allowed_shipping_methods.all()}
    if allowed and (shipping_method or "") not in allowed:
        raise HttpError(
            400, "Payment method not available for this shipping method")

    override = (pm.provider or "").strip().lower()
    if override:
        if override not in _PROVIDER_MAP:
            raise HttpError(400, "Unsupported payment_method")
        provider = _PROVIDER_MAP[override]
    return provider, pm


def _payment_instructions_for_order(*, order) -> str:
    """Instructions shown on the order page for offline providers."""
    pi = getattr(order, "payment_intent", None)
    provider = (getattr(pi, "provider", "") or "").strip()
    country_code = (getattr(order, "country_code", "") or "").strip().upper()

    if provider in {
        PaymentIntent.Provider.BANK_TRANSFER, PaymentIntent.Provider.COD,
    }:
        code = "cod" if provider == PaymentIntent.Provider.COD else "bank_transfer"
        try:
            from payments.models import PaymentMethod

            pm = (
                PaymentMethod.objects.filter(is_active=True, code=code)
                .filter(
                    models.Q(country_code="")
                    | models.Q(country_code=country_code))
                .order_by("-country_code")
                .first()
            )
            if pm:
                return pm.instructions_for_order(order_id=order.id)
        except Exception:
            pass
        if provider == PaymentIntent.Provider.BANK_TRANSFER:
            return _bank_transfer_instructions_for(
                order_id=order.id, country_code=country_code)
    return ""


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


def _require_user(request):
    user = request.auth
    if not user:
        raise HttpError(401, "Unauthorized")
    return user


def _user_from_cookie_if_present(request):
    # Cart endpoints must work without auth; but if access cookie is present, use it.

    # Storefront identity = JWT cookie. It must win over the Django session:
    # cookies are domain-scoped (not port-scoped), so a browser logged into
    # admin on another port sends `sessionid` here too — a different user
    # would hijack the cart/favorites identity.
    try:
        from accounts.jwt_utils import decode_token
        from django.contrib.auth import get_user_model
        from django.conf import settings

        cookie_name = getattr(settings, "AUTH_COOKIE_ACCESS_NAME", "access_token")
        token = (request.COOKIES.get(cookie_name) or "").strip()
        if token:
            payload = decode_token(token)
            if payload.get("type") == "access" and payload.get("sub"):
                User = get_user_model()
                u = User.objects.filter(id=int(payload["sub"]), is_active=True).first()
                if u is not None:
                    return u
    except Exception:
        pass

    # Allow normal Django session auth as fallback
    try:
        u = getattr(request, "user", None)
        if u is not None and getattr(u, "is_authenticated", False):
            return u
    except Exception:
        pass

    return None


def _get_cart_for_request(request, *, create: bool) -> Cart | None:
    _ensure_shop_context_if_strict(request)
    user = _user_from_cookie_if_present(request)
    shop_id = _request_shop_id(request)

    session_key = ""
    if hasattr(request, "session"):
        try:
            session_key = request.session.session_key or ""
        except Exception:
            session_key = ""

    if user:
        user_cart_qs = Cart.objects.filter(user=user)
        if shop_id is not None:
            user_cart_qs = user_cart_qs.filter(shop_id=shop_id)
        else:
            user_cart_qs = user_cart_qs.filter(shop__isnull=True)
        user_cart = user_cart_qs.first()

        guest_cart = None
        if session_key:
            guest_qs = Cart.objects.filter(user=None, session_key=session_key)
            if shop_id is not None:
                guest_qs = guest_qs.filter(shop_id=shop_id)
            else:
                guest_qs = guest_qs.filter(shop__isnull=True)
            guest_cart = guest_qs.first()

        if not user_cart:
            # Allow merge on first GET after login if a guest cart exists.
            if create or guest_cart:
                user_cart, _ = Cart.objects.get_or_create(
                    user=user,
                    shop_id=shop_id,
                    defaults={"session_key": "", "shop_id": shop_id},
                )
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
        guest_qs = Cart.objects.filter(user=None, session_key=session_key)
        if shop_id is not None:
            guest_qs = guest_qs.filter(shop_id=shop_id)
        else:
            guest_qs = guest_qs.filter(shop__isnull=True)
        return guest_qs.first()

    cart, _ = Cart.objects.get_or_create(
        user=None,
        session_key=session_key,
        shop_id=shop_id,
        defaults={"shop_id": shop_id},
    )
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
    "unisend": _resolve_pickup_unisend,
    "lpexpress": _resolve_pickup_unisend,
}


def _validate_and_resolve_pickup(*, shipping_method: str, pickup_point_id: str | None, country_code: str):
    from shipping.models import ShippingMethod

    shipping_method = (shipping_method or "").strip() or "unisend_pickup"
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
    shop_id: int | None = None,
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

    channel_type = "b2b" if customer_group_id else "retail"
    list_unit_net = resolve_shop_variant_price(
        variant=v,
        shop_id=shop_id,
        channel_type=channel_type,
        customer_group_id=customer_group_id,
    ).unit_net
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
    shop_id: int | None = None,
    language_code: str | None = None,
) -> tuple[list[CartItemOut], MoneyOut, dict | None]:
    out_items: list[CartItemOut] = []

    # Vienas prefetch visoms eilutėms — options, varianto/produkto nuotraukos.
    models.prefetch_related_objects(
        items,
        "variant__option_values__option_type",
        "variant__option_values__option_value",
        "variant__variant_images",
        "variant__product__images",
    )

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
            shop_id=shop_id,
        )

        if it.offer_id:
            stock_available = inventory_available_for_offer(offer_id=it.offer_id)
        else:
            stock_available = inventory_available_for_variant(variant_id=v.id)

        total_net += line_total.net
        total_vat += line_total.vat
        total_gross += line_total.gross

        option_rows = sorted(
            v.option_values.all(),
            key=lambda r: (r.option_type.sort_order, r.option_type.code),
        )
        options_out = [
            CartItemOptionOut(
                option_type_code=r.option_type.code,
                option_type_name=localize_option_type(
                    r.option_type, language_code=language_code
                ),
                option_value_code=r.option_value.code,
                option_value_label=localize_option_value(
                    r.option_value, language_code=language_code
                ),
            )
            for r in option_rows
        ]
        variant_name = v.name or " / ".join(o.option_value_label for o in options_out)

        v_imgs = sorted(v.variant_images.all(), key=lambda i: (i.sort_order, i.id))
        p_imgs = [] if not v.product_id else list(v.product.images.all()[:1])
        img = v_imgs[0] if v_imgs else (p_imgs[0] if p_imgs else None)
        image = image_out(img, context="listing") if img else None

        out_items.append(
            CartItemOut(
                id=it.id,
                variant_id=v.id,
                offer_id=(int(it.offer_id) if it.offer_id else None),
                sku=v.sku,
                name=(v.product.name if v.product_id else v.sku),
                product_slug=(v.product.slug if v.product_id else ""),
                variant_name=variant_name,
                options=options_out,
                image=image,
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


# Re-export everything (incl. underscore helpers) so endpoint modules can use
# `from .shared import *` after the mechanical split of the original api.py.
__all__ = [n for n in dir() if not n.startswith("__")]
