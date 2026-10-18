"""RMA (returns) ops endpoints."""

from __future__ import annotations

from ninja import Query, Schema
from ninja.errors import HttpError

from checkout.models import ReturnLine, ReturnRequest
from checkout.returns import (
    create_return,
    create_return_shipment,
    credit_return,
    line_is_returnable,
    line_return_days,
    receive_return,
    returned_qty_map,
    send_return_instructions,
    set_return_status,
)

from .shared import (
    _auth,
    _ensure_shop_context_if_strict,
    _get_order_for_ops,
    _request_shop_id,
    _require_ops_user,
    _to_iso,
    _track_order_event,
    OrderEvent,
    router,
)


class OpsReturnLineIn(Schema):
    order_line_id: int
    qty: int
    reason: str = ""


class OpsReturnCreateIn(Schema):
    lines: list[OpsReturnLineIn]
    note: str = ""
    approve: bool = True
    allow_expired: bool = False


class OpsReturnReceiveLineIn(Schema):
    return_line_id: int
    qty: int


class OpsReturnReceiveIn(Schema):
    lines: list[OpsReturnReceiveLineIn]
    restock_warehouse_id: int | None = None


class OpsReturnShipmentIn(Schema):
    carrier_code: str = ""
    tracking_number: str = ""
    notes: str = ""


class OpsReturnCreditIn(Schema):
    discount_gross: str | None = None
    send_email: bool = False


class OpsReturnLineOut(Schema):
    id: int
    order_line_id: int
    sku: str = ""
    name: str = ""
    qty: int
    received_qty: int
    credited_qty: int
    reason: str = ""
    returnable: bool = True
    return_days: int = 0


class OpsReturnOut(Schema):
    id: int
    number: str
    status: str
    order_id: int
    note: str = ""
    shipment_id: int | None = None
    tracking_number: str = ""
    credit_invoice_id: int | None = None
    credit_invoice_number: str = ""
    decided_at: str = ""
    received_at: str = ""
    completed_at: str = ""
    created_at: str = ""
    lines: list[OpsReturnLineOut] = []


def _serialize_return(ret: ReturnRequest) -> OpsReturnOut:
    return OpsReturnOut(
        id=int(ret.id),
        number=ret.number,
        status=ret.status,
        order_id=int(ret.order_id),
        note=ret.note or "",
        shipment_id=ret.shipment_id,
        tracking_number=(
            ret.shipment.tracking_number if ret.shipment_id else ""),
        credit_invoice_id=ret.credit_invoice_id,
        credit_invoice_number=(
            ret.credit_invoice.number if ret.credit_invoice_id else ""),
        decided_at=_to_iso(ret.decided_at),
        received_at=_to_iso(ret.received_at),
        completed_at=_to_iso(ret.completed_at),
        created_at=_to_iso(ret.created_at),
        lines=[
            OpsReturnLineOut(
                id=int(rl.id),
                order_line_id=int(rl.order_line_id),
                sku=rl.order_line.sku or "",
                name=rl.order_line.name or "",
                qty=int(rl.qty),
                received_qty=int(rl.received_qty),
                credited_qty=int(rl.credited_qty),
                reason=rl.reason or "",
                returnable=line_is_returnable(rl.order_line),
                return_days=line_return_days(rl.order_line),
            )
            for rl in ret.lines.select_related(
                "order_line", "order_line__variant__product").all()
        ],
    )


def _return_for_ops(request, return_id: int) -> ReturnRequest:
    qs = ReturnRequest.objects.select_related(
        "order", "shipment", "credit_invoice")
    shop_id = _request_shop_id(request)
    if shop_id is not None:
        qs = qs.filter(order__shop_id=shop_id)
    ret = qs.filter(id=return_id).first()
    if ret is None:
        raise HttpError(404, "Return not found")
    return ret


class OpsReturnSearchIn(Schema):
    q: str = ""
    status: str = ""
    order_id: int | None = None
    limit: int = 50


@router.get("/returns", response=list[OpsReturnOut], auth=_auth)
def ops_list_returns(request, filters: OpsReturnSearchIn = Query(...)):
    _ensure_shop_context_if_strict(request)
    _require_ops_user(request)
    qs = ReturnRequest.objects.select_related(
        "order", "shipment", "credit_invoice")
    shop_id = _request_shop_id(request)
    if shop_id is not None:
        qs = qs.filter(order__shop_id=shop_id)
    if filters.order_id:
        qs = qs.filter(order_id=filters.order_id)
    if filters.status:
        qs = qs.filter(status=filters.status)
    if (filters.q or "").strip():
        from django.db.models import Q
        q = filters.q.strip()
        qs = qs.filter(
            Q(number__icontains=q)
            | Q(order__id__iexact=q.lstrip("#"))
            | Q(lines__order_line__sku__icontains=q)
            | Q(shipment__tracking_number__icontains=q)
        ).distinct()
    limit = max(1, min(int(filters.limit or 50), 200))
    return [_serialize_return(r) for r in qs[:limit]]


@router.post("/orders/{order_id}/returns", response=OpsReturnOut, auth=_auth)
def ops_create_return(request, order_id: int, payload: OpsReturnCreateIn):
    _ensure_shop_context_if_strict(request)
    user = _require_ops_user(request)
    order = _get_order_for_ops(order_id, shop_id=_request_shop_id(request))
    try:
        ret = create_return(
            order,
            lines=[ln.model_dump() for ln in payload.lines],
            note=payload.note,
            approve=payload.approve,
            allow_expired=payload.allow_expired,
            user=user,
        )
    except ValueError as exc:
        raise HttpError(400, str(exc))
    if ret.status == ReturnRequest.Status.APPROVED:
        send_return_instructions(ret)
    _track_order_event(
        order=order, user=user, kind=OrderEvent.Kind.NOTE,
        name="ops_return_created",
        payload={
            "return_id": ret.id, "number": ret.number,
            "lines": [ln.model_dump() for ln in payload.lines],
        },
    )
    return _serialize_return(ret)


@router.get("/returns/{return_id}", response=OpsReturnOut, auth=_auth)
def ops_return_detail(request, return_id: int):
    _ensure_shop_context_if_strict(request)
    _require_ops_user(request)
    return _serialize_return(_return_for_ops(request, return_id))


@router.post("/returns/{return_id}/status/{status}",
             response=OpsReturnOut, auth=_auth)
def ops_return_status(request, return_id: int, status: str):
    _ensure_shop_context_if_strict(request)
    user = _require_ops_user(request)
    ret = _return_for_ops(request, return_id)
    try:
        ret = set_return_status(ret, status, user=user)
    except ValueError as exc:
        raise HttpError(400, str(exc))
    if status == ReturnRequest.Status.APPROVED:
        send_return_instructions(ret)
    _track_order_event(
        order=ret.order, user=user, kind=OrderEvent.Kind.NOTE,
        name="ops_return_status",
        payload={"return_id": ret.id, "status": status},
    )
    return _serialize_return(ret)


@router.post("/returns/{return_id}/shipment",
             response=OpsReturnOut, auth=_auth)
def ops_return_shipment(request, return_id: int, payload: OpsReturnShipmentIn):
    _ensure_shop_context_if_strict(request)
    user = _require_ops_user(request)
    ret = _return_for_ops(request, return_id)
    try:
        create_return_shipment(
            ret,
            carrier_code=payload.carrier_code,
            tracking_number=payload.tracking_number,
            notes=payload.notes,
            user=user,
        )
    except ValueError as exc:
        raise HttpError(400, str(exc))
    _track_order_event(
        order=ret.order, user=user, kind=OrderEvent.Kind.NOTE,
        name="ops_return_shipment",
        payload={"return_id": ret.id,
                 "tracking_number": payload.tracking_number},
    )
    return _serialize_return(ret)


@router.post("/returns/{return_id}/receive",
             response=OpsReturnOut, auth=_auth)
def ops_return_receive(request, return_id: int, payload: OpsReturnReceiveIn):
    _ensure_shop_context_if_strict(request)
    user = _require_ops_user(request)
    ret = _return_for_ops(request, return_id)
    try:
        ret = receive_return(
            ret,
            items=[ln.model_dump() for ln in payload.lines],
            restock_warehouse_id=payload.restock_warehouse_id,
            user=user,
        )
    except ValueError as exc:
        raise HttpError(400, str(exc))
    _track_order_event(
        order=ret.order, user=user, kind=OrderEvent.Kind.NOTE,
        name="ops_return_received",
        payload={
            "return_id": ret.id,
            "lines": [ln.model_dump() for ln in payload.lines],
            "restock_warehouse_id": payload.restock_warehouse_id,
        },
    )
    return _serialize_return(ret)


@router.post("/returns/{return_id}/credit",
             response=OpsReturnOut, auth=_auth)
def ops_return_credit(request, return_id: int, payload: OpsReturnCreditIn):
    _ensure_shop_context_if_strict(request)
    user = _require_ops_user(request)
    ret = _return_for_ops(request, return_id)
    try:
        credit = credit_return(
            ret,
            discount_gross=payload.discount_gross,
            send_email=payload.send_email,
            user=user,
        )
    except ValueError as exc:
        raise HttpError(400, str(exc))
    _track_order_event(
        order=ret.order, user=user, kind=OrderEvent.Kind.NOTE,
        name="ops_return_credited",
        payload={"return_id": ret.id, "credit_invoice": credit.number},
    )
    return _serialize_return(ret)
