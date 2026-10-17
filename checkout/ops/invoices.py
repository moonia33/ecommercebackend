from __future__ import annotations

from django.http import FileResponse, Http404
from ninja import Schema
from ninja.errors import HttpError

from billing.models import Invoice
from billing.services import (
    cancel_invoice,
    ensure_invoice_pdf,
    issue_credit_invoice,
    issue_invoice_for_order,
    send_invoice_email,
)

from .shared import (
    OpsActionResultOut,
    _auth,
    _ensure_shop_context_if_strict,
    _get_order_for_ops,
    _request_shop_id,
    _require_ops_user,
    _to_iso,
    router,
)


class OpsInvoiceLineOut(Schema):
    id: int
    line_type: str
    name: str
    sku: str = ""
    qty: int
    unit_net: str = "0"
    vat_rate: str = "0"
    total_gross: str = "0"
    credited_qty: int = 0


class OpsInvoiceOut(Schema):
    id: int
    number: str
    kind: str
    status: str
    order_id: int
    issue_date: str
    currency: str
    total_gross: str = ""
    language_code: str = ""
    emailed_at: str = ""
    has_pdf: bool = False
    credit_of_number: str = ""


class OpsInvoiceDetailOut(OpsInvoiceOut):
    lines: list[OpsInvoiceLineOut] = []


class OpsCreditLineIn(Schema):
    invoice_line_id: int
    qty: int


class OpsCreditInvoiceIn(Schema):
    # null/empty -> full credit of every invoice line
    lines: list[OpsCreditLineIn] | None = None
    send_email: bool = False


def _serialize_invoice(inv: Invoice) -> OpsInvoiceOut:
    return OpsInvoiceOut(
        id=int(inv.id),
        number=inv.number,
        kind=inv.kind,
        status=inv.status,
        order_id=int(inv.order_id),
        issue_date=inv.issue_date.isoformat(),
        currency=inv.currency or "EUR",
        total_gross=(inv.totals or {}).get("gross", ""),
        language_code=inv.language_code or "",
        emailed_at=_to_iso(inv.emailed_at),
        has_pdf=bool(inv.pdf),
        credit_of_number=inv.credit_of.number if inv.credit_of_id else "",
    )


def _invoice_for_ops(request, invoice_id: int) -> Invoice:
    qs = Invoice.objects.select_related("order", "credit_of")
    shop_id = _request_shop_id(request)
    if shop_id is not None:
        qs = qs.filter(shop_id=shop_id)
    inv = qs.filter(id=invoice_id).first()
    if inv is None:
        raise HttpError(404, "Invoice not found")
    return inv


@router.get("/invoices", response=list[OpsInvoiceOut], auth=_auth)
def ops_list_invoices(request, order_id: int | None = None, limit: int = 50):
    _ensure_shop_context_if_strict(request)
    _require_ops_user(request)
    qs = Invoice.objects.select_related("order", "credit_of")
    shop_id = _request_shop_id(request)
    if shop_id is not None:
        qs = qs.filter(shop_id=shop_id)
    if order_id:
        qs = qs.filter(order_id=order_id)
    limit = max(1, min(int(limit or 50), 200))
    return [_serialize_invoice(i) for i in qs[:limit]]


@router.get("/invoices/{invoice_id}", response=OpsInvoiceDetailOut, auth=_auth)
def ops_invoice_detail(request, invoice_id: int):
    _ensure_shop_context_if_strict(request)
    _require_ops_user(request)
    inv = _invoice_for_ops(request, invoice_id)

    # Cumulative credited qty per source line (issued credit notes only).
    from billing.models import InvoiceLine
    credited: dict[int, int] = {}
    for cl in InvoiceLine.objects.filter(
            invoice__credit_of=inv,
            invoice__status=Invoice.Status.ISSUED,
            credited_line__isnull=False):
        credited[cl.credited_line_id] = (
            credited.get(cl.credited_line_id, 0) + int(cl.qty))

    return OpsInvoiceDetailOut(
        **_serialize_invoice(inv).model_dump(),
        lines=[
            OpsInvoiceLineOut(
                id=int(ln.id),
                line_type=ln.line_type,
                name=ln.name or "",
                sku=ln.sku or "",
                qty=int(ln.qty),
                unit_net=str(ln.unit_net),
                vat_rate=str(ln.vat_rate),
                total_gross=str(ln.total_gross),
                credited_qty=credited.get(ln.id, 0),
            )
            for ln in inv.lines.all()
        ],
    )


@router.post("/orders/{order_id}/invoice", response=OpsInvoiceOut, auth=_auth)
def ops_issue_order_invoice(request, order_id: int):
    """Issue the sales invoice now (idempotent — returns the existing one)."""
    _ensure_shop_context_if_strict(request)
    _require_ops_user(request)
    order = _get_order_for_ops(order_id, shop_id=_request_shop_id(request))
    inv = issue_invoice_for_order(order)
    return _serialize_invoice(inv)


@router.post("/invoices/{invoice_id}/credit", response=OpsInvoiceOut, auth=_auth)
def ops_credit_invoice(request, invoice_id: int, payload: OpsCreditInvoiceIn):
    """Issue a full or partial credit invoice against a sales invoice."""
    _ensure_shop_context_if_strict(request)
    _require_ops_user(request)
    inv = _invoice_for_ops(request, invoice_id)

    try:
        credit = issue_credit_invoice(
            inv,
            lines=(
                [ln.model_dump() for ln in payload.lines]
                if payload.lines else None
            ),
        )
    except ValueError as exc:
        raise HttpError(400, str(exc))

    try:
        ensure_invoice_pdf(credit)
    except Exception:
        pass  # PDF can be regenerated lazily via /pdf

    if payload.send_email:
        send_invoice_email(credit)

    return _serialize_invoice(credit)


@router.post("/invoices/{invoice_id}/cancel", response=OpsInvoiceOut, auth=_auth)
def ops_cancel_invoice(request, invoice_id: int):
    """Mark an invoice cancelled (kept for audit; frees re-issue for sales)."""
    _ensure_shop_context_if_strict(request)
    _require_ops_user(request)
    inv = _invoice_for_ops(request, invoice_id)
    inv = cancel_invoice(inv)
    return _serialize_invoice(inv)


@router.post("/invoices/{invoice_id}/resend", response=OpsActionResultOut, auth=_auth)
def ops_resend_invoice(request, invoice_id: int):
    _ensure_shop_context_if_strict(request)
    _require_ops_user(request)
    inv = _invoice_for_ops(request, invoice_id)
    ok = send_invoice_email(inv)
    return OpsActionResultOut(
        ok=ok,
        message=("Invoice email queued" if ok else "No buyer email / send failed"),
        updated_ids=[inv.id] if ok else [],
        failed_ids=[] if ok else [inv.id],
    )


@router.get("/invoices/{invoice_id}/pdf", auth=_auth)
def ops_invoice_pdf(request, invoice_id: int):
    _ensure_shop_context_if_strict(request)
    _require_ops_user(request)
    inv = _invoice_for_ops(request, invoice_id)
    if not inv.pdf:
        try:
            ensure_invoice_pdf(inv)
        except Exception:
            raise HttpError(409, "PDF unavailable")
    if not inv.pdf:
        raise Http404("Invoice has no PDF")
    return FileResponse(
        inv.pdf.open("rb"),
        content_type="application/pdf",
        filename=f"{inv.number}.pdf",
    )
