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
    buyer_name: str = ""
    buyer_email: str = ""


class OpsInvoiceListOut(Schema):
    page: int
    page_size: int
    total_count: int
    results: list[OpsInvoiceOut]


class OpsInvoiceDetailOut(OpsInvoiceOut):
    lines: list[OpsInvoiceLineOut] = []


class OpsCreditLineIn(Schema):
    invoice_line_id: int
    qty: int


class OpsCreditInvoiceIn(Schema):
    # null/empty -> full credit of every invoice line
    lines: list[OpsCreditLineIn] | None = None
    # explicit discount clawback (gross); default = proportional share
    discount_gross: str | None = None
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
        buyer_name=(
            (inv.buyer or {}).get("company")
            or (inv.buyer or {}).get("full_name") or ""),
        buyer_email=(inv.buyer or {}).get("email") or "",
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


def _filtered_invoices(
    request, *,
    q: str = "", kind: str = "", status: str = "",
    order_id: int | None = None, date_from: str = "", date_to: str = "",
    invoice_ids: list[int] | None = None,
):
    """Shared scoping+filtering for the registry list and bulk export."""
    from django.db.models import Q

    qs = Invoice.objects.select_related("order", "credit_of")
    shop_id = _request_shop_id(request)
    if shop_id is not None:
        qs = qs.filter(shop_id=shop_id)
    if invoice_ids:
        qs = qs.filter(id__in=[int(i) for i in invoice_ids])
        return qs
    if order_id:
        qs = qs.filter(order_id=order_id)
    if kind.strip():
        qs = qs.filter(kind=kind.strip())
    if status.strip():
        qs = qs.filter(status=status.strip())
    q = (q or "").strip()
    if q:
        qs = qs.filter(
            Q(number__icontains=q)
            | Q(buyer__email__icontains=q)
            | Q(buyer__full_name__icontains=q)
            | Q(buyer__company__icontains=q)
            | Q(order__id__iexact=q.lstrip("#"))
        ).distinct()
    if (date_from or "").strip():
        qs = qs.filter(issue_date__gte=date_from.strip())
    if (date_to or "").strip():
        qs = qs.filter(issue_date__lte=date_to.strip())
    return qs


@router.get("/invoices/registry", response=OpsInvoiceListOut, auth=_auth)
def ops_invoices_registry(
    request,
    q: str = "",
    kind: str = "",
    status: str = "",
    order_id: int | None = None,
    date_from: str = "",
    date_to: str = "",
    page: int = 1,
    page_size: int = 50,
):
    """Paginated document registry for the /dokumentai admin page."""
    _ensure_shop_context_if_strict(request)
    _require_ops_user(request)

    qs = _filtered_invoices(
        request, q=q, kind=kind, status=status, order_id=order_id,
        date_from=date_from, date_to=date_to)

    page = max(1, int(page or 1))
    page_size = max(1, min(int(page_size or 50), 100))
    offset = (page - 1) * page_size
    total_count = qs.count()
    rows = list(qs.order_by("-issue_date", "-id")[offset : offset + page_size])
    return OpsInvoiceListOut(
        page=page, page_size=page_size, total_count=total_count,
        results=[_serialize_invoice(i) for i in rows])


EXPORT_MAX_INVOICES = 300


class OpsInvoiceExportIn(Schema):
    # pdf_zip = archive of invoice PDFs; csv = flat document-level rows
    # for accounting-software import (column layout until the target
    # program's format is known).
    format: str = "pdf_zip"
    invoice_ids: list[int] = []
    q: str = ""
    kind: str = ""
    status: str = ""
    order_id: int | None = None
    date_from: str = ""
    date_to: str = ""


@router.post("/invoices/export", auth=_auth)
def ops_invoices_export(request, payload: OpsInvoiceExportIn):
    """Bulk download: ZIP of PDFs or a flat CSV.

    Selection: explicit `invoice_ids`, or the same filter set as the
    registry. Capped at EXPORT_MAX_INVOICES per request.
    """
    _ensure_shop_context_if_strict(request)
    _require_ops_user(request)

    fmt = (payload.format or "pdf_zip").strip().lower()
    if fmt not in {"pdf_zip", "csv"}:
        raise HttpError(400, f"Unsupported export format '{payload.format}'")

    qs = _filtered_invoices(
        request, q=payload.q, kind=payload.kind, status=payload.status,
        order_id=payload.order_id, date_from=payload.date_from,
        date_to=payload.date_to,
        invoice_ids=payload.invoice_ids or None,
    ).order_by("issue_date", "id")

    if qs.count() > EXPORT_MAX_INVOICES:
        raise HttpError(
            400,
            f"Per daug dokumentų (>{EXPORT_MAX_INVOICES}) — "
            "susiaurinkite filtrus arba eksportuokite dalimis")
    invoices = list(qs)
    if not invoices:
        raise HttpError(404, "Nėra dokumentų pagal nurodytus kriterijus")

    if fmt == "csv":
        return _invoices_csv_response(invoices)
    return _invoices_zip_response(invoices)


def _invoices_zip_response(invoices: list[Invoice]) -> FileResponse:
    import io
    import zipfile

    buf = io.BytesIO()
    skipped: list[str] = []
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        used: set[str] = set()
        for inv in invoices:
            if not inv.pdf:
                try:
                    ensure_invoice_pdf(inv)
                except Exception:
                    pass
            if not inv.pdf:
                skipped.append(inv.number)
                continue
            name = f"{inv.number}.pdf"
            i = 2
            while name in used:
                name = f"{inv.number}_{i}.pdf"
                i += 1
            used.add(name)
            with inv.pdf.open("rb") as fp:
                zf.writestr(name, fp.read())
    if not used:
        raise HttpError(
            409,
            "Nė vienas dokumentas neturi PDF "
            f"(praleista: {', '.join(skipped)})")
    buf.seek(0)
    return FileResponse(
        buf, content_type="application/zip",
        filename="saskaitos.zip")


def _invoices_csv_response(invoices: list[Invoice]) -> FileResponse:
    import csv
    import io

    buf = io.StringIO()
    # UTF-8 BOM + ';' delimiter: opens correctly in Excel (LT locale) and
    # is accepted by most accounting-software CSV importers.
    buf.write("﻿")
    writer = csv.writer(buf, delimiter=";", lineterminator="\r\n")
    writer.writerow([
        "number", "kind", "status", "issue_date", "order_id",
        "credit_of", "buyer_name", "buyer_company", "buyer_vat_code",
        "buyer_email", "buyer_country", "currency",
        "total_net", "total_vat", "total_gross",
    ])
    for inv in invoices:
        buyer = inv.buyer or {}
        totals = inv.totals or {}
        writer.writerow([
            inv.number, inv.kind, inv.status,
            inv.issue_date.isoformat(), inv.order_id,
            inv.credit_of.number if inv.credit_of_id else "",
            buyer.get("full_name") or "",
            buyer.get("company") or "",
            buyer.get("company_vat_code") or "",
            buyer.get("email") or "",
            buyer.get("country_code") or "",
            inv.currency or "EUR",
            totals.get("net", ""), totals.get("vat", ""),
            totals.get("gross", ""),
        ])
    return FileResponse(
        io.BytesIO(buf.getvalue().encode("utf-8")),
        content_type="text/csv; charset=utf-8",
        filename="saskaitos.csv")


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
                if payload.lines is not None else None
            ),
            discount_gross=payload.discount_gross,
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
