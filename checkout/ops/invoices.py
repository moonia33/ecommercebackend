from __future__ import annotations

from django.http import FileResponse, Http404
from ninja import Schema
from ninja.errors import HttpError

from billing.models import Invoice
from billing.services import ensure_invoice_pdf, send_invoice_email

from .shared import (
    OpsActionResultOut,
    _auth,
    _ensure_shop_context_if_strict,
    _request_shop_id,
    _require_ops_user,
    _to_iso,
    router,
)


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
