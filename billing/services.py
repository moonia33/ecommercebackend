from __future__ import annotations

import logging
from decimal import Decimal
from typing import Any

from django.conf import settings
from django.core.files.base import ContentFile
from django.db import transaction
from django.template.loader import render_to_string
from django.utils import timezone

from checkout.models import Order

from .models import Invoice, InvoiceLine, InvoiceSeries, ShopBillingProfile

logger = logging.getLogger(__name__)


def _shop_profile(shop) -> ShopBillingProfile | None:
    if shop is None:
        return None
    return getattr(shop, "billing_profile", None)


def _seller_snapshot(order: Order) -> dict[str, Any]:
    shop = order.shop
    profile = _shop_profile(shop)
    if profile:
        return {
            "legal_name": profile.legal_name or (shop.name if shop else ""),
            "company_code": profile.company_code,
            "vat_code": profile.vat_code,
            "address_line1": profile.address_line1,
            "city": profile.city,
            "postal_code": profile.postal_code,
            "country_code": profile.country_code,
            "email": profile.email,
            "phone": profile.phone,
            "bank_name": profile.bank_name,
            "iban": profile.iban,
            "swift": profile.swift,
            "footer": profile.invoice_footer,
        }
    return {"legal_name": shop.name if shop else ""}


def _buyer_snapshot(order: Order) -> dict[str, Any]:
    user = order.user
    return {
        "full_name": order.shipping_full_name or "",
        "company": order.shipping_company or "",
        "company_vat_code": "",
        "address_line1": order.shipping_line1 or "",
        "city": order.shipping_city or "",
        "postal_code": order.shipping_postal_code or "",
        "country_code": order.shipping_country_code or order.country_code or "",
        "phone": order.shipping_phone or "",
        "email": getattr(user, "email", "") or "",
    }


@transaction.atomic
def _next_invoice_number(shop, kind: str, year: int) -> str:
    """Atomically allocate the next number in the shop/kind/year series."""
    profile = _shop_profile(shop)
    prefix = (
        (profile.credit_prefix if kind == Invoice.Kind.CREDIT
         else profile.invoice_prefix) if profile else ""
    ) or ("KRF" if kind == Invoice.Kind.CREDIT else "SF")
    digits = int(profile.number_digits) if profile and profile.number_digits else 4

    series, _ = InvoiceSeries.objects.select_for_update().get_or_create(
        shop=shop, kind=kind, year=year, defaults={"next_number": 1})
    number = f"{prefix}-{year}-{str(series.next_number).zfill(digits)}"
    series.next_number += 1
    series.save(update_fields=["next_number"])
    return number


def _order_language(order: Order) -> str:
    shop = order.shop
    return (
        getattr(shop, "default_language_code", "")
        or getattr(settings, "LANGUAGE_CODE", "lt")
        or "lt"
    )


def _invoice_totals(lines: list[InvoiceLine]) -> dict[str, str]:
    net = sum((ln.total_net for ln in lines), Decimal("0.00"))
    vat = sum((ln.total_vat for ln in lines), Decimal("0.00"))
    gross = sum((ln.total_gross for ln in lines), Decimal("0.00"))
    return {"net": str(net), "vat": str(vat), "gross": str(gross)}


def _build_lines(invoice: Invoice, order: Order) -> list[InvoiceLine]:
    lines: list[InvoiceLine] = []
    for i, ln in enumerate(order.lines.all().order_by("id")):
        lines.append(InvoiceLine(
            invoice=invoice,
            line_type=InvoiceLine.LineType.PRODUCT,
            name=ln.name, sku=ln.sku, qty=ln.qty,
            unit_net=ln.unit_net, vat_rate=ln.vat_rate,
            total_net=ln.total_net, total_vat=ln.total_vat,
            total_gross=ln.total_gross, sort_order=i,
        ))
    sort = len(lines)
    if (order.shipping_gross or 0) > 0:
        lines.append(InvoiceLine(
            invoice=invoice,
            line_type=InvoiceLine.LineType.SHIPPING,
            name="Pristatymas", sku=(order.shipping_method or ""), qty=1,
            unit_net=order.shipping_net, vat_rate=Decimal("0"),
            total_net=order.shipping_net, total_vat=order.shipping_vat,
            total_gross=order.shipping_gross, sort_order=sort,
        ))
        sort += 1
    for fee in order.fees.all():
        lines.append(InvoiceLine(
            invoice=invoice,
            line_type=InvoiceLine.LineType.FEE,
            name=fee.name, sku=fee.code or "", qty=1,
            unit_net=fee.net, vat_rate=fee.vat_rate,
            total_net=fee.net, total_vat=fee.vat,
            total_gross=fee.gross, sort_order=sort,
        ))
        sort += 1
    for disc in order.discounts.all():
        lines.append(InvoiceLine(
            invoice=invoice,
            line_type=InvoiceLine.LineType.DISCOUNT,
            name=disc.name or "Nuolaida", sku=disc.code or "", qty=1,
            unit_net=-disc.net, vat_rate=Decimal("0"),
            total_net=-disc.net, total_vat=-disc.vat,
            total_gross=-disc.gross, sort_order=sort,
        ))
        sort += 1
    return lines


@transaction.atomic
def issue_invoice_for_order(order: Order) -> Invoice:
    """Idempotent: returns existing active sales invoice if present."""
    existing = Invoice.objects.filter(
        order=order, kind=Invoice.Kind.SALES,
        status=Invoice.Status.ISSUED).first()
    if existing:
        return existing

    today = timezone.localdate()
    invoice = Invoice(
        kind=Invoice.Kind.SALES,
        order=order,
        shop=order.shop,
        issue_date=today,
        currency=order.currency or "EUR",
        language_code=_order_language(order),
        seller=_seller_snapshot(order),
        buyer=_buyer_snapshot(order),
    )
    invoice.number = _next_invoice_number(order.shop, Invoice.Kind.SALES, today.year)
    invoice.save()

    lines = _build_lines(invoice, order)
    InvoiceLine.objects.bulk_create(lines)
    invoice.totals = _invoice_totals(lines)
    invoice.save(update_fields=["totals"])
    return invoice


def invoice_template_name(invoice: Invoice) -> str:
    profile = _shop_profile(invoice.shop)
    if profile and profile.pdf_template:
        return profile.pdf_template
    return "billing/invoice.html"


def render_invoice_pdf(invoice: Invoice) -> bytes:
    """HTML -> PDF via WeasyPrint (lazy import: optional on dev machines)."""
    from weasyprint import HTML  # noqa: PLC0415

    html = render_to_string(invoice_template_name(invoice), {
        "invoice": invoice,
        "lines": invoice.lines.all(),
        "order": invoice.order,
        "seller": invoice.seller or {},
        "buyer": invoice.buyer or {},
        "totals": invoice.totals or {},
    })
    return HTML(string=html).write_pdf()


def ensure_invoice_pdf(invoice: Invoice) -> bool:
    """Render + store PDF once. Returns True if a file now exists."""
    if invoice.pdf:
        return True
    pdf_bytes = render_invoice_pdf(invoice)
    invoice.pdf.save(f"{invoice.number}.pdf",
                     ContentFile(pdf_bytes), save=True)
    return True


def send_invoice_email(invoice: Invoice) -> bool:
    from notifications.services import send_templated_email

    to_email = (invoice.buyer or {}).get("email") or ""
    if not to_email:
        logger.warning("invoice %s: no buyer email", invoice.number)
        return False

    attachment = ""
    if invoice.pdf:
        attachment = invoice.pdf.name
    res = send_templated_email(
        template_key="invoice_issued",
        to_email=to_email,
        language_code=invoice.language_code,
        context={
            "invoice_number": invoice.number,
            "issue_date": invoice.issue_date.isoformat(),
            "total_gross": (invoice.totals or {}).get("gross", ""),
            "currency": invoice.currency,
            "order_id": invoice.order_id,
        },
        attachments=[attachment] if attachment else [],
    )
    if res.ok:
        invoice.emailed_at = timezone.now()
        invoice.save(update_fields=["emailed_at"])
    return res.ok
