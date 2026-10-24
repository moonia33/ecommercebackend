from __future__ import annotations

from .shared import *


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
def payment_methods(request, country_code: str = "LT",
                    shipping_method: str = ""):
    _ensure_shop_context_if_strict(request)
    _require_user(request)
    country_code = (country_code or "").strip().upper() or "LT"
    shipping_method = (shipping_method or "").strip()
    try:
        from payments.models import PaymentMethod
        from payments.models import ShopPaymentMethodPolicy

        shop_id = _request_shop_id(request)
        methods = list(
            PaymentMethod.objects.filter(is_active=True)
            .filter(
                models.Q(country_code="") | models.Q(country_code=country_code)
            )
            .prefetch_related("allowed_shipping_methods")
            .order_by("sort_order", "code")
        )
        if shipping_method:
            # Methods restricted to specific shipping methods only apply
            # when the chosen one is allowed (e.g. COD for store pickup).
            methods = [
                m for m in methods
                if not m.allowed_shipping_methods.exists()
                or m.allowed_shipping_methods.filter(
                    code=shipping_method).exists()
            ]
        if shop_id:
            policy_rows = list(
                ShopPaymentMethodPolicy.objects.filter(shop_id=shop_id, is_active=True).filter(
                    models.Q(country_code="") | models.Q(country_code=country_code)
                )
            )
            if policy_rows:
                policy_by_method_id = {int(p.payment_method_id): p for p in policy_rows}
                methods = [m for m in methods if int(m.id) in policy_by_method_id]
                for m in methods:
                    p = policy_by_method_id.get(int(m.id))
                    if p and (p.name_override or "").strip():
                        m.name = p.name_override
                methods.sort(key=lambda m: (
                    int(
                        policy_by_method_id.get(int(m.id)).sort_order_override
                        if policy_by_method_id.get(int(m.id)) and policy_by_method_id.get(int(m.id)).sort_order_override is not None
                        else m.sort_order
                    ),
                    m.code,
                ))
    except Exception:
        methods = []

    if methods:
        out: list[PaymentMethodOut] = []
        for m in methods:
            logo_url = ""
            try:
                if getattr(m, "image", None) and getattr(m.image, "url", None):
                    logo_url = str(m.image.url)
            except Exception:
                logo_url = ""
            out.append(
                PaymentMethodOut(
                    code=m.code,
                    name=m.name,
                    kind=m.kind,
                    provider=m.provider,
                    instructions=(m.instructions or "").strip(),
                    logo_url=logo_url,
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
            logo_url="",
        ),
    ]


@router.get("/payment-options", response=list[PaymentOptionOut], auth=_auth)
def payment_options(request, country_code: str = "LT",
                    shipping_method: str = ""):
    _ensure_shop_context_if_strict(request)
    _require_user(request)
    country_code = (country_code or "").strip().upper() or "LT"

    # 1) Base payment methods from DB (or fallback)
    base = payment_methods(request, country_code=country_code,
                           shipping_method=shipping_method)

    options: list[PaymentOptionOut] = []
    _base_rank: dict[str, int] = {}

    for idx, m in enumerate(base):
        _base_rank[str(m.code)] = idx
        # Dovanų kortelė — ne radio opcija: stored-value instrumentas
        # pritaikomas kodo laukelyje (split tender), ne pasirenkamas kaip
        # vienintelis mokėjimo būdas.
        if str(m.code).strip().lower() == "gift_card":
            continue
        opt_payload: dict = {"payment_method": str(m.code)}
        prov = str(m.provider or m.code or "").strip().lower()
        if prov == "inbank":
            expanded = _inbank_options(m)
            if expanded:
                options.extend(expanded)
                continue
            # Fallback — vienas generinis metodas su kalkuliatoriumi.
            opt_payload["calculator_url"] = (
                "/api/v1/payments/inbank/calculation")
        if prov == "montonio":
            expanded = _montonio_options(m, country_code=country_code)
            if expanded:
                options.extend(expanded)
                continue
        options.append(
            PaymentOptionOut(
                id=str(m.code),
                kind="method",
                title=str(m.name),
                provider=str(m.provider or m.code or ""),
                instructions=str(getattr(m, "instructions", "") or ""),
                logo_url=str(getattr(m, "logo_url", "") or ""),
                payload=opt_payload,
            )
        )

    # Defensive dedupe (prevents FE keyed each issues if upstream ever duplicates ids)
    unique: list[PaymentOptionOut] = []
    seen_ids: set[str] = set()
    for o in options:
        oid = str(getattr(o, "id", "") or "")
        if not oid or oid in seen_ids:
            continue
        seen_ids.add(oid)
        unique.append(o)
    options = unique

    # Common ordering for all payment options.
    # - Payment methods first
    # - bank_transfer always last (regardless of sort_order)
    def _option_sort_key(o: PaymentOptionOut) -> tuple:
        payload = getattr(o, "payload", None) or {}
        payment_method = str(payload.get("payment_method") or "").strip().lower()
        oid = str(getattr(o, "id", "") or "").strip().lower()

        if payment_method == "bank_transfer" or oid == "bank_transfer":
            return (999, 0, oid)

        kind = str(getattr(o, "kind", "") or "")
        if kind == "method":
            pm_code = str(payload.get("payment_method") or "").strip()
            return (0, int(_base_rank.get(pm_code, 10**9)), oid)

        # Fallback: keep unknown kinds after methods, before bank_transfer
        return (2, 0, oid)

    options.sort(key=_option_sort_key)
    return options


# Montonio metodų pavadinimai UI'ui (bankams naudojami jų vardai)
_MONTONIO_METHOD_LABELS = {
    "cardPayments": "Banko kortelė",
    "applePay": "Apple Pay",
    "googlePay": "Google Pay",
    "mobilePay": "MobilePay",
    "blik": "BLIK",
    "bnpl": "Pirkti dabar, mokėti vėliau",
    "hirePurchase": "Išsimokėtinai",
}


def _montonio_options(pay_method, *, country_code: str) -> list[PaymentOptionOut]:
    """Išskleidžia 'montonio' metodą į konkrečius bankus + kitus metodus.

    Grąžina [] kai API nepasiekiamas — caller tada palieka bendrą opciją.
    """
    from payments import montonio as montonio_svc

    data = montonio_svc.get_payment_methods()
    methods = data.get("paymentMethods") if isinstance(data, dict) else None
    if not isinstance(methods, dict):
        return []

    out: list[PaymentOptionOut] = []
    base_payload = {"payment_method": str(pay_method.code)}

    # 1) Bankai (paymentInitiation) — regiono sąrašas pagal country_code
    setup = ((methods.get("paymentInitiation") or {}).get("setup") or {})
    banks: list[dict] = []
    region = setup.get(country_code)
    if not region and country_code != "LT":
        region = setup.get("LT")
    if not region and setup:
        region = next(iter(setup.values()))
    if region:
        banks = sorted(
            region.get("paymentMethods") or [],
            key=lambda b: int(b.get("uiPosition") or 999),
        )
    for bank in banks:
        code = str(bank.get("code") or "").strip()
        if not code:
            continue
        out.append(PaymentOptionOut(
            id=f"montonio:{code}",
            kind="method",
            title=str(bank.get("name") or code),
            provider="montonio",
            logo_url=str(bank.get("logoUrl") or ""),
            payload={
                **base_payload,
                "montonio_method": "paymentInitiation",
                "preferred_provider": code,
            },
        ))

    # 2) Kiti įjungti metodai (kortelės, Apple Pay, ...)
    for mkey, minfo in methods.items():
        if mkey == "paymentInitiation" or not isinstance(minfo, dict):
            continue
        out.append(PaymentOptionOut(
            id=f"montonio:{mkey}",
            kind="method",
            title=_MONTONIO_METHOD_LABELS.get(mkey, mkey),
            provider="montonio",
            logo_url=str(minfo.get("logoUrl") or ""),
            payload={**base_payload, "montonio_method": mkey},
        ))

    return out


def _inbank_options(pay_method) -> list[PaymentOptionOut]:
    """Išskleidžia 'inbank' metodą į finansavimo produktus.

    [] kai Inbank nesukonfigūruotas/API nepasiekiamas — caller palieka
    bendrą opciją su calculator_url.
    """
    from payments import inbank as inbank_svc

    products = inbank_svc.get_products_cached()
    if not products:
        return []

    out: list[PaymentOptionOut] = []
    for p in products:
        if not isinstance(p, dict):
            continue
        code = str(p.get("productCode") or p.get("code") or "").strip()
        if not code:
            continue
        title = str(
            p.get("name") or p.get("displayName") or p.get("description")
            or "Išsimokėtinai")
        payload = {
            "payment_method": str(pay_method.code),
            "inbank_product_code": code,
            "calculator_url": "/api/v1/payments/inbank/calculation",
        }
        # Suma/periodo ribos FE'ui (kalkuliatorius + validacija)
        for k_src, k_dst in (
            ("minAmount", "min_amount"), ("maxAmount", "max_amount"),
            ("minPeriod", "min_period"), ("maxPeriod", "max_period"),
            ("allowedPeriods", "allowed_periods"),
        ):
            if p.get(k_src) is not None:
                payload[k_dst] = p[k_src]
        out.append(PaymentOptionOut(
            id=f"inbank:{code}",
            kind="method",
            title=title,
            provider="inbank",
            logo_url=str(p.get("logoUrl") or ""),
            payload=payload,
        ))
    return out


@router.get("/shipping-methods", response=list[ShippingMethodOut], auth=_auth)
def shipping_methods(request, country_code: str = "LT"):
    _ensure_shop_context_if_strict(request)
    _require_user(request)
    country_code = (country_code or "").strip().upper()
    if len(country_code) != 2:
        raise HttpError(400, "Invalid country_code")

    from shipping.models import ShippingMethod, ShippingRate, ShopShippingMethodPolicy

    tax_class = get_shipping_tax_class()
    if not tax_class:
        vat_rate = Decimal("0")
    else:
        vat_rate = get_vat_rate(country_code=country_code, tax_class=tax_class)

    methods = list(
        ShippingMethod.objects.filter(
            is_active=True).order_by("sort_order", "code")
    )
    shop_id = _request_shop_id(request)
    policy_by_method_id: dict[int, ShopShippingMethodPolicy] = {}
    if shop_id:
        pols = list(
            ShopShippingMethodPolicy.objects.filter(shop_id=shop_id, is_active=True).filter(
                models.Q(country_code="") | models.Q(country_code=country_code)
            )
        )
        if pols:
            policy_by_method_id = {int(p.method_id): p for p in pols}
            methods = [m for m in methods if int(m.id) in policy_by_method_id]
            methods.sort(
                key=lambda m: (
                    int(
                        policy_by_method_id.get(int(m.id)).sort_order_override
                        if policy_by_method_id.get(int(m.id)) and policy_by_method_id.get(int(m.id)).sort_order_override is not None
                        else m.sort_order
                    ),
                    m.code,
                )
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
        p = policy_by_method_id.get(int(m.id))
        if p and p.net_eur_override is not None:
            shipping_net = Decimal(p.net_eur_override)

        price = money_from_net(
            currency="EUR", unit_net=shipping_net, vat_rate=vat_rate, qty=1)

        image_url = ""
        try:
            img = getattr(m, "image", None)
            if img and getattr(img, "url", None):
                image_url = request.build_absolute_uri(img.url)
        except Exception:
            image_url = ""

        pickup_points_url = ""
        try:
            if bool(getattr(m, "requires_pickup_point", False)):
                carrier = (getattr(m, "carrier_code", "") or "").strip().lower()
                if carrier == "dpd":
                    pickup_points_url = request.build_absolute_uri(
                        f"/api/v1/dpd/lockers?country_code={country_code}"
                    )
                elif carrier in {"unisend", "lpexpress"}:
                    pickup_points_url = request.build_absolute_uri(
                        f"/api/v1/unisend/terminals?country_code={country_code}"
                    )
        except Exception:
            pickup_points_url = ""

        out.append(
            ShippingMethodOut(
                code=m.code,
                name=m.name,
                carrier_code=getattr(m, "carrier_code", "") or "",
                requires_pickup_point=bool(
                    getattr(m, "requires_pickup_point", False)),
                image_url=image_url,
                pickup_points_url=pickup_points_url,
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
