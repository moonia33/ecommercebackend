from __future__ import annotations

from .shared import *


@router.post("/apply-pickup-point", response=ApplyPickupPointOut, auth=_auth)
def apply_pickup_point(request, payload: ApplyPickupPointIn):
    _ensure_shop_context_if_strict(request)
    user = _require_user(request)

    shipping_method = (payload.shipping_method or "").strip()
    pickup_point_id = (payload.pickup_point_id or "").strip()
    if not shipping_method or not pickup_point_id:
        raise HttpError(400, "shipping_method and pickup_point_id are required")

    full_name = _full_name_for_user(user)
    if not full_name:
        raise HttpError(400, "Missing first_name/last_name")

    phone = _primary_phone_for_user(user)
    if not phone:
        raise HttpError(400, "Missing phone")

    # Resolve pickup point; this validates pickup_point_id for the carrier.
    carrier_code, pickup_locker, pickup_snapshot = _validate_and_resolve_pickup(
        shipping_method=shipping_method,
        pickup_point_id=pickup_point_id,
        country_code="",
    )

    addr_fields = _pickup_point_address_fields(
        carrier_code=carrier_code,
        pickup_locker=pickup_locker,
        pickup_snapshot=pickup_snapshot,
    )
    country_code = (addr_fields.get("country_code") or "").strip().upper() or "LT"
    if len(country_code) != 2:
        country_code = "LT"

    label_name = (addr_fields.get("label_name") or "").strip()
    from shipping.models import ShippingMethod

    method = ShippingMethod.objects.filter(code=shipping_method).first()
    method_name = (getattr(method, "name", "") or "").strip()
    technical_label = f"{shipping_method}:{label_name}".strip(":")
    human_label = f"{(method_name or shipping_method)}:{label_name}".strip(":")
    addr_label = human_label
    line1 = (addr_fields.get("line1") or "").strip()
    city = (addr_fields.get("city") or "").strip()
    postal_code = (addr_fields.get("postal_code") or "").strip()
    if not line1 or not city or not postal_code:
        raise HttpError(400, "Pickup point address is incomplete")

    with transaction.atomic():
        has_default_billing = UserAddress.objects.filter(user=user, is_default_billing=True).exists()
        UserAddress.objects.filter(user=user, is_default_shipping=True).update(is_default_shipping=False)
        if not has_default_billing:
            UserAddress.objects.filter(user=user, is_default_billing=True).update(is_default_billing=False)

        # Avoid creating duplicate pickup-point addresses (common during checkout/preview flows).
        # Match by shipping_method label prefix + resolved address fields.
        existing = (
            UserAddress.objects.filter(user=user)
            .filter(
                label__in=[addr_label[:50], technical_label[:50]],
                line1=line1,
                city=city,
                postal_code=postal_code,
                country_code=country_code,
            )
            .order_by("-updated_at")
            .first()
        )

        if existing:
            addr = existing
            # Keep name/phone up to date with current user profile.
            addr.full_name = full_name
            addr.phone = phone
            addr.label = addr_label[:50]
            addr.is_default_shipping = True
            if not has_default_billing:
                addr.is_default_billing = True
                addr.save(
                    update_fields=[
                        "label",
                        "full_name",
                        "phone",
                        "is_default_shipping",
                        "is_default_billing",
                        "updated_at",
                    ]
                )
            else:
                addr.save(update_fields=["label", "full_name", "phone", "is_default_shipping", "updated_at"])
        else:
            addr = UserAddress.objects.create(
                user=user,
                label=addr_label[:50],
                full_name=full_name,
                company="",
                company_reg_no="",
                company_vat_no="",
                line1=line1,
                city=city,
                postal_code=postal_code,
                country_code=country_code,
                phone=phone,
                is_default_shipping=True,
                is_default_billing=(not has_default_billing),
            )

        if bool(getattr(payload, "set_as_primary_pickup_point", True)):
            UserPickupPoint.objects.update_or_create(
                user=user,
                defaults={
                    "shipping_method_code": shipping_method,
                    "pickup_point_id": pickup_point_id,
                    "pickup_point_name": str(addr_fields.get("label_name") or "").strip(),
                    "pickup_point_raw": (
                        (pickup_locker.raw if pickup_locker is not None else (pickup_snapshot or {}).get("pickup_point_raw"))
                        or {}
                    ),
                    "country_code": country_code,
                },
            )

    return {"shipping_address_id": int(addr.id)}


@router.post("/preview", response=CheckoutPreviewOut, auth=_auth)
def checkout_preview(request, payload: CheckoutPreviewIn):
    _ensure_shop_context_if_strict(request)
    user = _require_user(request)

    addr = UserAddress.objects.filter(
        user=user, id=payload.shipping_address_id).first()
    if not addr:
        raise HttpError(404, "Shipping address not found")

    shipping_method = (payload.shipping_method or "").strip() or "unisend_pickup"
    payment_method = (getattr(payload, "payment_method", "") or "").strip() or "bank_transfer"
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
            shop_id=_request_shop_id(request),
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
        shop_id=_request_shop_id(request),
        language_code=get_request_language_code(request),
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
                shop_id=_request_shop_id(request),
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


@router.post("/confirm", response=CheckoutConfirmOut, auth=_auth)
def checkout_confirm(request, payload: CheckoutConfirmIn):
    _ensure_shop_context_if_strict(request)
    user = _require_user(request)

    addr = UserAddress.objects.filter(
        user=user, id=payload.shipping_address_id).first()
    if not addr:
        raise HttpError(404, "Shipping address not found")

    shipping_method = (payload.shipping_method or "").strip() or "unisend_pickup"
    pickup_point_id = _maybe_fill_pickup_point_id_from_user(
        user=user,
        shipping_method=shipping_method,
        pickup_point_id=getattr(payload, "pickup_point_id", None),
    )

    payment_method = (payload.payment_method or "").strip() or "bank_transfer"
    payment_country = (
        getattr(addr, "country_code", "") or getattr(addr, "country", "") or ""
    ).strip().upper()
    provider, pay_method = _resolve_checkout_payment_method(
        code=payment_method,
        country_code=payment_country,
        shipping_method=shipping_method,
    )
    if provider == PaymentIntent.Provider.MONTONIO:
        from payments import montonio as montonio_svc
        if not montonio_svc.is_configured():
            raise HttpError(400, "Payment method not available")
        _montonio_methods = {
            "paymentInitiation", "cardPayments", "applePay", "googlePay",
            "mobilePay", "blik", "bnpl", "hirePurchase",
        }
        if (payload.montonio_method or "").strip() and \
                payload.montonio_method.strip() not in _montonio_methods:
            raise HttpError(400, "Invalid payment method option")
    elif provider == PaymentIntent.Provider.INBANK:
        from payments import inbank as inbank_svc
        if not inbank_svc.is_configured():
            raise HttpError(400, "Payment method not available")

    idem_key = (request.headers.get("Idempotency-Key") or "").strip()

    channel = (getattr(payload, "channel", "normal") or "normal").strip().lower()
    if channel not in {"normal", "outlet"}:
        raise HttpError(400, "Invalid channel")
    coupon_code = (getattr(payload, "coupon_code", None) or "").strip().lower() or None

    shop_id = _request_shop_id(request)
    if idem_key:
        existing_qs = Order.objects.filter(user=user, idempotency_key=idem_key)
        if shop_id is not None:
            existing_qs = existing_qs.filter(shop_id=shop_id)
        else:
            existing_qs = existing_qs.filter(shop__isnull=True)
        existing = existing_qs.first()
        if existing:
            pi = getattr(existing, "payment_intent", None)
            return CheckoutConfirmOut(
                order_id=existing.id,
                payment_provider=(pi.provider if pi else "bank_transfer"),
                payment_status=(pi.status if pi else "pending"),
                redirect_url=(pi.redirect_url if pi else ""),
                payment_instructions=_payment_instructions_for_order(
                    order=existing),
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
            shop_id=_request_shop_id(request),
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
                shop_id=_request_shop_id(request),
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

        # Clear cart after creating the order.
        CartItem.objects.filter(cart=cart).delete()

        payment_instructions = ""
        if provider in {
            PaymentIntent.Provider.MONTONIO, PaymentIntent.Provider.INBANK,
        }:
            pass  # redirect url created below, outside the transaction
        elif pay_method is not None:
            payment_instructions = pay_method.instructions_for_order(
                order_id=order.id)
        elif provider == PaymentIntent.Provider.BANK_TRANSFER:
            payment_instructions = _bank_transfer_instructions_for(
                order_id=order.id, country_code=order.country_code
            )

    if pi.provider == PaymentIntent.Provider.MONTONIO:
        from payments import montonio as montonio_svc
        try:
            from api.i18n import get_request_language_code
            res = montonio_svc.create_order_payment(
                order=order,
                language_code=get_request_language_code(request),
                preferred_provider=(
                    (payload.payment_provider or "").strip() or None),
                method=(
                    (payload.montonio_method or "").strip()
                    or "paymentInitiation"),
            )
        except montonio_svc.MontonioError as exc:
            pi.status = PaymentIntent.Status.FAILED
            pi.save(update_fields=["status", "updated_at"])
            raise HttpError(502, "Payment provider error") from exc
        pi.external_id = res.uuid
        pi.redirect_url = res.payment_url
        pi.status = PaymentIntent.Status.REDIRECTED
        pi.raw_request = {**(pi.raw_request or {}), "montonio_order": res.raw_response}
        pi.save(update_fields=["external_id", "redirect_url", "status", "raw_request", "updated_at"])
    elif pi.provider == PaymentIntent.Provider.INBANK:
        from payments import inbank as inbank_svc
        try:
            from api.i18n import get_request_language_code
            session = inbank_svc.create_pos_session(
                order=order,
                locale=f"{get_request_language_code(request) or 'lt'}-LT",
                product_code_override=(
                    (payload.inbank_product_code or "").strip() or None),
            )
        except inbank_svc.InbankError as exc:
            pi.status = PaymentIntent.Status.FAILED
            pi.save(update_fields=["status", "updated_at"])
            raise HttpError(502, "Payment provider error") from exc
        pi.external_id = session.uuid
        pi.redirect_url = session.redirect_url
        pi.status = PaymentIntent.Status.REDIRECTED
        pi.raw_request = {**(pi.raw_request or {}), "inbank_session": session.raw}
        pi.save(update_fields=["external_id", "redirect_url", "status", "raw_request", "updated_at"])

    try:
        purchase_lines = [
            {"product_id": int(ol.variant.product_id), "qty": int(ol.qty)}
            for ol in order.lines.select_related("variant")
            if ol.variant_id and ol.variant
        ]
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
                "lines": purchase_lines,
            },
            country_code=str(order.country_code or ""),
            channel=str(channel or ""),
            shop_id=_request_shop_id(request),
            outbox_providers=["newsman"],
        )
    except Exception:
        pass

    return CheckoutConfirmOut(
        order_id=order.id,
        payment_provider=pi.provider,
        payment_status=pi.status,
        redirect_url=pi.redirect_url or "",
        payment_instructions=payment_instructions,
    )
