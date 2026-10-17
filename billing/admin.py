from __future__ import annotations

from django.contrib import admin, messages

from .models import Invoice, InvoiceLine, InvoiceSeries, ShopBillingProfile
from .services import ensure_invoice_pdf, send_invoice_email


class InvoiceLineInline(admin.TabularInline):
    model = InvoiceLine
    extra = 0
    can_delete = False
    fields = ("line_type", "name", "sku", "qty",
              "unit_net", "vat_rate", "total_gross")
    readonly_fields = fields
    max_num = 0


@admin.register(ShopBillingProfile)
class ShopBillingProfileAdmin(admin.ModelAdmin):
    list_display = ("shop", "legal_name", "company_code",
                    "invoice_prefix", "credit_prefix", "number_digits")
    search_fields = ("shop__name", "shop__code", "legal_name")


@admin.register(InvoiceSeries)
class InvoiceSeriesAdmin(admin.ModelAdmin):
    list_display = ("shop", "kind", "year", "next_number")
    list_filter = ("kind", "year")


@admin.register(Invoice)
class InvoiceAdmin(admin.ModelAdmin):
    list_display = ("number", "kind", "status", "order",
                    "issue_date", "emailed_at")
    list_filter = ("kind", "status", "issue_date")
    search_fields = ("number", "order__id")
    readonly_fields = ("number", "kind", "order", "shop", "credit_of",
                       "issue_date", "currency", "language_code",
                       "seller", "buyer", "totals", "pdf", "emailed_at",
                       "issued_at", "created_at")
    inlines = [InvoiceLineInline]
    actions = ["regenerate_pdf", "resend_email"]

    @admin.action(description="Persiųsti sąskaitą el. paštu")
    def resend_email(self, request, queryset):
        sent = 0
        for inv in queryset:
            if send_invoice_email(inv):
                sent += 1
        self.message_user(request, f"Persiųsta sąskaitų: {sent}",
                          level=messages.SUCCESS)

    @admin.action(description="Sugeneruoti PDF iš naujo (jei trūksta)")
    def regenerate_pdf(self, request, queryset):
        done = 0
        for inv in queryset.filter(pdf=""):
            ensure_invoice_pdf(inv)
            done += 1
        self.message_user(request, f"Sugeneruota PDF: {done}",
                          level=messages.SUCCESS)
