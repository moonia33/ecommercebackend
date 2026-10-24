from __future__ import annotations

from .shared import *


@router.get("/payments", response=OpsPaymentListOut, auth=_auth)
def ops_list_payments(
    request,
    status: str = "",
    provider: str = "",
    order_id: int | None = None,
    page: int = 1,
    page_size: int = 50,
):
    _ensure_shop_context_if_strict(request)
    _require_ops_user(request)
    shop_id = _request_shop_id(request)
    page = max(1, int(page or 1))
    page_size = max(1, min(int(page_size or 50), 100))
    offset = (page - 1) * page_size

    qs = PaymentIntent.objects.select_related("order")
    if shop_id is not None:
        qs = qs.filter(order__shop_id=shop_id)
    else:
        qs = qs.filter(order__shop__isnull=True)
    if status:
        qs = qs.filter(status=status)
    if provider:
        qs = qs.filter(provider=provider)
    if order_id is not None:
        qs = qs.filter(order_id=order_id)

    total_count = qs.count()
    rows = list(qs.order_by("-created_at", "-id")[offset : offset + page_size])
    out = [
        OpsPaymentOut(
            id=int(pi.id),
            order_id=int(pi.order_id),
            provider=(pi.provider or ""),
            status=(pi.status or ""),
            amount_gross=str(pi.amount_gross),
            currency=(pi.currency or ""),
            external_id=(pi.external_id or ""),
            created_at=_to_iso(pi.created_at),
            updated_at=_to_iso(pi.updated_at),
        )
        for pi in rows
    ]
    return OpsPaymentListOut(page=page, page_size=page_size, total_count=total_count, results=out)


@router.get("/payments/{payment_id}", response=OpsPaymentOut, auth=_auth)
def ops_payment_detail(request, payment_id: int):
    _ensure_shop_context_if_strict(request)
    _require_ops_user(request)
    shop_id = _request_shop_id(request)
    qs = PaymentIntent.objects.select_related("order").filter(id=payment_id)
    if shop_id is not None:
        qs = qs.filter(order__shop_id=shop_id)
    else:
        qs = qs.filter(order__shop__isnull=True)
    pi = qs.first()
    if pi is None:
        raise HttpError(404, "Payment not found")
    return OpsPaymentOut(
        id=int(pi.id),
        order_id=int(pi.order_id),
        provider=(pi.provider or ""),
        status=(pi.status or ""),
        amount_gross=str(pi.amount_gross),
        currency=(pi.currency or ""),
        external_id=(pi.external_id or ""),
        created_at=_to_iso(pi.created_at),
        updated_at=_to_iso(pi.updated_at),
    )


@router.post("/payments/{payment_id}/reconcile", response=OpsActionResultOut, auth=_auth)
def ops_payment_reconcile(request, payment_id: int, payload: OpsPaymentActionIn):
    _ensure_shop_context_if_strict(request)
    user = _require_ops_user(request)
    shop_id = _request_shop_id(request)
    qs = PaymentIntent.objects.select_related("order").filter(id=payment_id)
    if shop_id is not None:
        qs = qs.filter(order__shop_id=shop_id)
    else:
        qs = qs.filter(order__shop__isnull=True)
    pi = qs.first()
    if pi is None:
        raise HttpError(404, "Payment not found")

    pi.raw_response = {
        **(pi.raw_response or {}),
        "ops_reconcile": {
            "note": payload.note,
            "by_user_id": int(user.id),
            "at": timezone.now().isoformat(),
        },
    }
    pi.save(update_fields=["raw_response", "updated_at"])
    _track_order_event(
        order=pi.order,
        user=user,
        kind=OrderEvent.Kind.PAYMENT,
        name="ops_payment_reconciled",
        payload={"payment_id": int(pi.id), "note": payload.note},
    )
    return OpsActionResultOut(ok=True, message="Payment reconciled", updated_ids=[pi.id], failed_ids=[])


@router.post("/payments/{payment_id}/mark-paid", response=OpsActionResultOut, auth=_auth)
def ops_payment_mark_paid(request, payment_id: int, payload: OpsPaymentActionIn):
    _ensure_shop_context_if_strict(request)
    user = _require_ops_user(request)
    shop_id = _request_shop_id(request)
    qs = PaymentIntent.objects.select_related("order").filter(id=payment_id)
    if shop_id is not None:
        qs = qs.filter(order__shop_id=shop_id)
    else:
        qs = qs.filter(order__shop__isnull=True)
    pi = qs.first()
    if pi is None:
        raise HttpError(404, "Payment not found")

    with transaction.atomic():
        pi.status = PaymentIntent.Status.SUCCEEDED
        pi.save(update_fields=["status", "updated_at"])
        pi.order.status = Order.Status.PAID
        pi.order.save(update_fields=["status", "updated_at"])
        finalize_paid_order(order_id=pi.order_id)

    _track_order_event(
        order=pi.order,
        user=user,
        kind=OrderEvent.Kind.PAYMENT,
        name="ops_payment_mark_paid",
        payload={"payment_id": int(pi.id), "note": payload.note},
    )
    return OpsActionResultOut(ok=True, message="Payment marked as paid", updated_ids=[pi.id], failed_ids=[])


@router.post("/payments/{payment_id}/refund", response=OpsActionResultOut, auth=_auth)
def ops_payment_refund(request, payment_id: int, payload: OpsPaymentActionIn):
    _ensure_shop_context_if_strict(request)
    user = _require_ops_user(request)
    shop_id = _request_shop_id(request)
    qs = PaymentIntent.objects.select_related("order").filter(id=payment_id)
    if shop_id is not None:
        qs = qs.filter(order__shop_id=shop_id)
    else:
        qs = qs.filter(order__shop__isnull=True)
    pi = qs.first()
    if pi is None:
        raise HttpError(404, "Payment not found")

    with transaction.atomic():
        pi.status = PaymentIntent.Status.CANCELLED
        pi.save(update_fields=["status", "updated_at"])
        pi.order.status = Order.Status.CANCELLED
        pi.order.save(update_fields=["status", "updated_at"])
        release_coupon_for_order(order_id=pi.order_id)
        release_inventory_for_order(order_id=pi.order_id)
        try:
            from giftcards.services import restore_card_debits_for_order
            restore_card_debits_for_order(order=pi.order)
        except Exception:
            pass

    _track_order_event(
        order=pi.order,
        user=user,
        kind=OrderEvent.Kind.PAYMENT,
        name="ops_payment_refund",
        payload={"payment_id": int(pi.id), "note": payload.note},
    )
    return OpsActionResultOut(ok=True, message="Refund processed", updated_ids=[pi.id], failed_ids=[])
