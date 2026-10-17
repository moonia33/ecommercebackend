from __future__ import annotations

from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone

from accounts.models import User
from api.models import Shop
from billing.models import (
    Invoice,
    InvoiceSeries,
    ShopBillingProfile,
)
from billing.services import (
    issue_invoice_for_order,
    send_invoice_email,
)
from checkout.models import Order, OrderLine
from notifications.models import EmailTemplate, OutboundEmail


class InvoiceBase(TestCase):
    def setUp(self):
        self.shop = Shop.objects.create(
            code="inv-shop", name="Invoice Shop",
            primary_domain="inv.local", default_language_code="lt")
        ShopBillingProfile.objects.create(
            shop=self.shop, legal_name="MB Test", company_code="123",
            vat_code="LT123", iban="LT0012345", bank_name="Bankas")
        self.user = User.objects.create_user(
            email="buyer@example.com", password="x")
        self.order = Order.objects.create(
            user=self.user, shop=self.shop,
            status=Order.Status.PAID,
            currency="EUR", country_code="LT",
            shipping_full_name="Test Buyer",
            shipping_line1="Gatve 1", shipping_city="Kaunas",
            shipping_postal_code="50000", shipping_country_code="LT",
            shipping_phone="+37060000000",
            items_net=Decimal("10.00"), items_vat=Decimal("2.10"),
            items_gross=Decimal("12.10"),
            shipping_net=Decimal("3.00"), shipping_vat=Decimal("0.63"),
            shipping_gross=Decimal("3.63"),
            total_net=Decimal("13.00"), total_vat=Decimal("2.73"),
            total_gross=Decimal("15.73"),
        )
        OrderLine.objects.create(
            order=self.order, sku="SKU1", name="Product 1",
            unit_net=Decimal("10.00"), vat_rate=Decimal("0.21"),
            qty=1, total_net=Decimal("10.00"),
            total_vat=Decimal("2.10"), total_gross=Decimal("12.10"),
            unit_vat=Decimal("2.10"), unit_gross=Decimal("12.10"),
        )


class InvoiceNumberingTests(InvoiceBase):
    def test_sequential_numbers_per_shop_year(self):
        inv1 = issue_invoice_for_order(self.order)
        o2 = Order.objects.create(
            user=self.user, shop=self.shop, status=Order.Status.PAID,
            currency="EUR", country_code="LT")
        inv2 = issue_invoice_for_order(o2)
        year = timezone.localdate().year
        self.assertEqual(inv1.number, f"SF-{year}-0001")
        self.assertEqual(inv2.number, f"SF-{year}-0002")

    def test_invoice_is_idempotent(self):
        inv1 = issue_invoice_for_order(self.order)
        inv2 = issue_invoice_for_order(self.order)
        self.assertEqual(inv1.id, inv2.id)
        self.assertEqual(Invoice.objects.count(), 1)

    def test_snapshot_is_immutable(self):
        inv = issue_invoice_for_order(self.order)
        self.assertEqual(inv.seller["legal_name"], "MB Test")
        self.assertEqual(inv.buyer["full_name"], "Test Buyer")
        self.assertEqual(inv.totals["gross"], "15.73")
        lines = list(inv.lines.all())
        self.assertEqual(len(lines), 2)  # product + shipping
        self.assertEqual(lines[0].name, "Product 1")
        self.assertEqual(lines[1].line_type, "shipping")

    def test_credit_prefix_from_profile(self):
        profile = self.shop.billing_profile
        series = InvoiceSeries.objects.create(
            shop=self.shop, kind="credit", year=2026, next_number=1)
        from billing.services import _next_invoice_number
        num = _next_invoice_number(self.shop, Invoice.Kind.CREDIT, 2026)
        self.assertTrue(num.startswith("KRF-2026-"))
        series.refresh_from_db()
        self.assertEqual(series.next_number, 2)


class DeliveredSignalTests(InvoiceBase):
    def test_delivered_transition_sets_delivered_at_and_queues_task(self):
        with patch("config.celery.safe_delay") as mock_delay:
            with self.captureOnCommitCallbacks(execute=True):
                self.order.delivery_status = Order.DeliveryStatus.DELIVERED
                self.order.save()
        self.order.refresh_from_db()
        self.assertIsNotNone(self.order.delivered_at)
        self.assertTrue(mock_delay.called)

    def test_at_pickup_point_does_not_set_delivered_at(self):
        with patch("config.celery.safe_delay") as mock_delay:
            with self.captureOnCommitCallbacks(execute=True):
                self.order.delivery_status = Order.DeliveryStatus.AT_PICKUP_POINT
                self.order.save()
        self.order.refresh_from_db()
        self.assertIsNone(self.order.delivered_at)
        self.assertFalse(mock_delay.called)

    def test_second_save_does_not_requeue(self):
        with patch("config.celery.safe_delay"):
            with self.captureOnCommitCallbacks(execute=True):
                self.order.delivery_status = Order.DeliveryStatus.DELIVERED
                self.order.save()
        delivered_first = self.order.delivered_at
        with patch("config.celery.safe_delay") as mock_delay:
            with self.captureOnCommitCallbacks(execute=True):
                self.order.save()
        self.assertFalse(mock_delay.called)
        self.assertEqual(self.order.delivered_at, delivered_first)


class InvoiceEmailTests(InvoiceBase):
    def test_email_queued_with_attachment(self):
        inv = issue_invoice_for_order(self.order)
        inv.pdf.name = "invoices/2026/SF-2026-0001.pdf"
        inv.save(update_fields=["pdf"])
        ok = send_invoice_email(inv)
        self.assertTrue(ok)
        outbound = OutboundEmail.objects.latest("id")
        self.assertEqual(outbound.to_email, "buyer@example.com")
        self.assertEqual(outbound.attachments, ["invoices/2026/SF-2026-0001.pdf"])
        self.assertEqual(outbound.template_key, "invoice_issued")
        inv.refresh_from_db()
        self.assertIsNotNone(inv.emailed_at)

    def test_invoice_templates_seeded(self):
        for lang in ("lt", "en", "pl"):
            self.assertTrue(
                EmailTemplate.objects.filter(
                    key="invoice_issued", language_code=lang).exists(),
                lang,
            )
