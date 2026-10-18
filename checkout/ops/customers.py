from __future__ import annotations

from .shared import *


def _customer_stats_filter(shop_id: int | None) -> Q:
    if shop_id is not None:
        return Q(orders__shop_id=shop_id)
    return Q(orders__shop__isnull=True)


def _serialize_customer_list_item(user, *, shop_id: int | None) -> OpsCustomerListItemOut:
    phones = list(getattr(user, "phones", []).all())
    primary_phone = next((p for p in phones if p.is_primary), None)
    phone = (getattr(primary_phone, "phone", "") or "") if primary_phone else (phones[0].phone if phones else "")

    groups = [g.name or g.code for g in user.get_active_customer_groups()]

    stats = _customer_order_stats(int(user.id), shop_id=shop_id) if not hasattr(user, "orders_count") else None

    return OpsCustomerListItemOut(
        id=int(user.id),
        email=(user.email or ""),
        first_name=(user.first_name or ""),
        last_name=(user.last_name or ""),
        full_name=_customer_full_name(user),
        phone=phone,
        is_active=bool(user.is_active),
        is_staff=bool(user.is_staff),
        date_joined=_to_iso(getattr(user, "date_joined", None)),
        last_login=_to_iso(getattr(user, "last_login", None)),
        orders_count=int(getattr(user, "orders_count", 0) or (stats.orders_count if stats else 0)),
        paid_orders_count=int(getattr(user, "paid_orders_count", 0) or (stats.paid_orders_count if stats else 0)),
        total_spent=_to_money_str(getattr(user, "total_spent", None) or (stats.total_spent if stats else "0")),
        last_order_at=_to_iso(getattr(user, "last_order_at", None) or (stats.last_order_at if stats else "")),
        groups=groups,
    )


@router.get("/customers", response=OpsCustomerListOut, auth=_auth)
def ops_list_customers(
    request,
    q: str = "",
    is_active: str = "",
    is_staff: str = "",
    has_orders: str = "",
    page: int = 1,
    page_size: int = 50,
):
    _ensure_shop_context_if_strict(request)
    _require_ops_user(request)
    shop_id = _request_shop_id(request)
    page = max(1, int(page or 1))
    page_size = max(1, min(int(page_size or 50), 100))
    offset = (page - 1) * page_size

    User = get_user_model()
    shop_filter = _customer_stats_filter(shop_id)

    qs = User.objects.prefetch_related("phones", "customer_groups").annotate(
        orders_count=Count("orders", filter=shop_filter, distinct=True),
        paid_orders_count=Count(
            "orders", filter=shop_filter & Q(orders__status=Order.Status.PAID), distinct=True
        ),
        total_spent=Sum("orders__total_gross", filter=shop_filter & Q(orders__status=Order.Status.PAID)),
        last_order_at=Max("orders__created_at", filter=shop_filter),
    )

    q = (q or "").strip()
    if q:
        qs = qs.filter(
            Q(email__icontains=q)
            | Q(first_name__icontains=q)
            | Q(last_name__icontains=q)
            | Q(phones__phone__icontains=q)
            | Q(orders__shipping_full_name__icontains=q)
        ).distinct()

    if (is_active or "").strip().lower() in {"true", "false"}:
        qs = qs.filter(is_active=is_active.strip().lower() == "true")
    if (is_staff or "").strip().lower() in {"true", "false"}:
        qs = qs.filter(is_staff=is_staff.strip().lower() == "true")
    if (has_orders or "").strip().lower() == "true":
        qs = qs.filter(orders_count__gt=0)

    total_count = qs.count()
    rows = list(
        qs.order_by(F("last_order_at").desc(nulls_last=True), "-date_joined")[
            offset : offset + page_size
        ]
    )
    return OpsCustomerListOut(
        page=page,
        page_size=page_size,
        total_count=total_count,
        results=[_serialize_customer_list_item(u, shop_id=shop_id) for u in rows],
    )


@router.get("/customers/{user_id}", response=OpsCustomerDetailOut, auth=_auth)
def ops_customer_detail(request, user_id: int):
    _ensure_shop_context_if_strict(request)
    _require_ops_user(request)
    shop_id = _request_shop_id(request)

    User = get_user_model()
    user = (
        User.objects.prefetch_related("phones", "addresses", "customer_groups")
        .filter(id=user_id)
        .first()
    )
    if user is None:
        raise HttpError(404, "Customer not found")

    orders_qs = (
        Order.objects.filter(user_id=user.id)
        .select_related("payment_intent")
        .annotate(items_count=Count("lines", distinct=True))
        .order_by("-created_at", "-id")
    )
    if shop_id is not None:
        orders_qs = orders_qs.filter(shop_id=shop_id)
    else:
        orders_qs = orders_qs.filter(shop__isnull=True)

    orders = [
        OpsCustomerOrderOut(
            id=int(o.id),
            status=(o.status or ""),
            payment_status=(
                (getattr(getattr(o, "payment_intent", None), "status", "") or "")
            ),
            delivery_status=(o.delivery_status or ""),
            created_at=_to_iso(o.created_at),
            amount_gross=_to_money_str(_effective_order_total_gross(o)),
            currency=(o.currency or "EUR"),
            items_count=int(getattr(o, "items_count", 0) or 0),
        )
        for o in orders_qs[:50]
    ]

    pickup_obj = getattr(user, "primary_pickup_point", None)
    pickup_point = None
    if pickup_obj is not None:
        pickup_point = OpsPickupPointOut(
            pickup_point_id=(pickup_obj.pickup_point_id or ""),
            pickup_point_name=(pickup_obj.pickup_point_name or ""),
            country_code=(pickup_obj.country_code or ""),
            raw=dict(pickup_obj.pickup_point_raw or {}),
        )

    stats = _customer_order_stats(int(user.id), shop_id=shop_id)
    base = _serialize_customer_list_item(user, shop_id=shop_id)

    return OpsCustomerDetailOut(
        **base.model_dump(exclude={
            "orders_count", "paid_orders_count",
            "total_spent", "last_order_at"}),
        orders_count=stats.orders_count,
        paid_orders_count=stats.paid_orders_count,
        total_spent=stats.total_spent,
        last_order_at=stats.last_order_at,
        phones=[
            OpsCustomerPhoneOut(
                phone=(p.phone or ""),
                label=(p.label or ""),
                is_primary=bool(p.is_primary),
                is_verified=bool(p.is_verified),
            )
            for p in user.phones.all()
        ],
        addresses=[
            OpsCustomerAddressOut(
                id=int(a.id),
                label=(a.label or ""),
                full_name=(a.full_name or ""),
                company=(a.company or ""),
                company_reg_no=(a.company_reg_no or ""),
                company_vat_no=(a.company_vat_no or ""),
                line1=(a.line1 or ""),
                city=(a.city or ""),
                postal_code=(a.postal_code or ""),
                country_code=(a.country_code or ""),
                phone=(a.phone or ""),
                is_default_shipping=bool(a.is_default_shipping),
                is_default_billing=bool(a.is_default_billing),
            )
            for a in user.addresses.all()
        ],
        pickup_point=pickup_point,
        orders=orders,
    )


@router.post("/customers/{user_id}/toggle-active", response=OpsActionResultOut, auth=_auth)
def ops_customer_toggle_active(request, user_id: int):
    _ensure_shop_context_if_strict(request)
    actor = _require_ops_user(request)

    User = get_user_model()
    user = User.objects.filter(id=user_id).first()
    if user is None:
        raise HttpError(404, "Customer not found")
    if user.is_superuser:
        raise HttpError(400, "Superuser accounts cannot be deactivated")
    if int(user.id) == int(actor.id):
        raise HttpError(400, "You cannot deactivate your own account")

    user.is_active = not bool(user.is_active)
    user.save(update_fields=["is_active"])
    state = "activated" if user.is_active else "deactivated"
    return OpsActionResultOut(ok=True, message=f"Customer {state}", updated_ids=[user.id], failed_ids=[])
