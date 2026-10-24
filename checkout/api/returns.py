"""Customer-facing RMA endpoints.

Ops-side lives in checkout/ops/returns.py. Here the customer only sees
their own order's returns and can open a new request (status=pending —
approved by ops, unlike ops' approve-by-default flow).
"""

from __future__ import annotations

from ninja import Schema
from ninja.errors import HttpError

from api.i18n import get_request_language_code
from checkout.models import ReturnRequest
from checkout.returns import create_return, set_return_status
from checkout.statuses import get_status_label

from .shared import *  # noqa: F401,F403
from .shared import (
    _auth,
    _ensure_shop_context_if_strict,
    _request_shop_id,
    router,
)


class ReturnLineIn(Schema):
    order_line_id: int
    qty: int
    reason: str = ""


class ReturnCreateIn(Schema):
    lines: list[ReturnLineIn]
    note: str = ""


class ReturnLineOut(Schema):
    id: int
    order_line_id: int
    sku: str
    name: str
    qty: int
    received_qty: int
    credited_qty: int
    reason: str


class ReturnOut(Schema):
    id: int
    number: str
    status: str
    status_label: str
    order_id: int
    note: str
    tracking_number: str
    credit_invoice_number: str
    created_at: str
    decided_at: str
    received_at: str
    completed_at: str
    lines: list[ReturnLineOut]


def _serialize_return(ret: ReturnRequest, language_code: str) -> ReturnOut:
    return ReturnOut(
        id=int(ret.id),
        number=ret.number,
        status=ret.status,
        status_label=get_status_label("return_status", ret.status, language_code),
        order_id=int(ret.order_id),
        note=ret.note or "",
        tracking_number=ret.shipment.tracking_number if ret.shipment_id else "",
        credit_invoice_number=(
            ret.credit_invoice.number if ret.credit_invoice_id else ""),
        created_at=ret.created_at.isoformat() if ret.created_at else "",
        decided_at=ret.decided_at.isoformat() if ret.decided_at else "",
        received_at=ret.received_at.isoformat() if ret.received_at else "",
        completed_at=ret.completed_at.isoformat() if ret.completed_at else "",
        lines=[
            ReturnLineOut(
                id=int(rl.id),
                order_line_id=int(rl.order_line_id),
                sku=rl.order_line.sku or "",
                name=rl.order_line.name or "",
                qty=int(rl.qty),
                received_qty=int(rl.received_qty),
                credited_qty=int(rl.credited_qty),
                reason=rl.reason or "",
            )
            for rl in ret.lines.select_related("order_line").all()
        ],
    )


def _own_return_qs(request):
    qs = ReturnRequest.objects.select_related(
        "order", "shipment", "credit_invoice")
    shop_id = _request_shop_id(request)
    if shop_id is not None:
        qs = qs.filter(order__shop_id=shop_id)
    else:
        qs = qs.filter(order__shop__isnull=True)
    return qs.filter(order__user=request.auth)


@router.get("/returns", response=list[ReturnOut], auth=_auth)
def list_returns(request):
    _ensure_shop_context_if_strict(request)
    lang = get_request_language_code(request)
    return [
        _serialize_return(r, lang)
        for r in _own_return_qs(request).order_by("-id")[:50]
    ]


@router.get("/returns/{return_id}", response=ReturnOut, auth=_auth)
def return_detail(request, return_id: int):
    _ensure_shop_context_if_strict(request)
    ret = _own_return_qs(request).filter(id=return_id).first()
    if ret is None:
        raise HttpError(404, "Return not found")
    return _serialize_return(ret, get_request_language_code(request))


@router.post("/orders/{order_id}/returns", response=ReturnOut, auth=_auth)
def create_return_request(request, order_id: int, payload: ReturnCreateIn):
    _ensure_shop_context_if_strict(request)
    shop_id = _request_shop_id(request)
    qs = Order.objects.filter(user=request.auth, id=order_id)
    qs = qs.filter(shop_id=shop_id) if shop_id is not None else qs.filter(shop__isnull=True)
    order = qs.first()
    if order is None:
        raise HttpError(404, "Order not found")
    try:
        ret = create_return(
            order,
            lines=[ln.model_dump() for ln in payload.lines],
            note=payload.note,
            approve=False,
            allow_expired=False,
            user=request.auth,
        )
    except ValueError as exc:
        raise HttpError(400, str(exc))
    return _serialize_return(ret, get_request_language_code(request))


@router.post("/returns/{return_id}/cancel", response=ReturnOut, auth=_auth)
def cancel_return(request, return_id: int):
    _ensure_shop_context_if_strict(request)
    ret = _own_return_qs(request).filter(id=return_id).first()
    if ret is None:
        raise HttpError(404, "Return not found")
    try:
        ret = set_return_status(
            ret, ReturnRequest.Status.CANCELLED, user=request.auth)
    except ValueError as exc:
        raise HttpError(400, str(exc))
    return _serialize_return(ret, get_request_language_code(request))
