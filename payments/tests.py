from __future__ import annotations

import json

import jwt
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from checkout.models import Order, PaymentIntent

User = get_user_model()

TEST_SECRET = "test-montonio-secret"


def _order_token(**overrides) -> str:
    payload = {
        "uuid": "m-uuid-123",
        "accessKey": "ak",
        "merchantReference": "1",
        "paymentStatus": "PAID",
        "grandTotal": 24.20,
        "currency": "EUR",
    }
    payload.update(overrides)
    return jwt.encode(payload, TEST_SECRET, algorithm="HS256")


@override_settings(MONTONIO_SECRET_KEY=TEST_SECRET, MONTONIO_ACCESS_KEY="ak")
class MontonioWebhookTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(email="m@example.com", password="x")
        self.order = Order.objects.create(
            user=self.user,
            status=Order.Status.PENDING_PAYMENT,
            total_gross="24.20",
            currency="EUR",
            country_code="LT",
        )
        self.pi = PaymentIntent.objects.create(
            order=self.order,
            provider=PaymentIntent.Provider.MONTONIO,
            status=PaymentIntent.Status.REDIRECTED,
            currency="EUR",
            amount_gross="24.20",
        )

    def _post(self, token: str):
        return self.client.post(
            "/api/v1/payments/montonio/webhook",
            data=json.dumps({"orderToken": token}),
            content_type="application/json",
        )

    def test_paid_webhook_marks_order_paid(self):
        res = self._post(_order_token(merchantReference=str(self.order.id)))
        self.assertEqual(res.status_code, 200)
        self.pi.refresh_from_db()
        self.order.refresh_from_db()
        self.assertEqual(self.pi.status, PaymentIntent.Status.SUCCEEDED)
        self.assertEqual(self.pi.external_id, "m-uuid-123")
        self.assertEqual(self.order.status, Order.Status.PAID)

    def test_webhook_is_idempotent(self):
        token = _order_token(merchantReference=str(self.order.id))
        self._post(token)
        res = self._post(token)
        self.assertEqual(res.status_code, 200)
        self.pi.refresh_from_db()
        self.assertEqual(self.pi.status, PaymentIntent.Status.SUCCEEDED)

    def test_invalid_signature_rejected(self):
        bad = jwt.encode(
            {"merchantReference": str(self.order.id), "paymentStatus": "PAID"},
            "wrong-secret", algorithm="HS256",
        )
        res = self._post(bad)
        self.assertEqual(res.status_code, 400)
        self.pi.refresh_from_db()
        self.assertEqual(self.pi.status, PaymentIntent.Status.REDIRECTED)

    def test_unknown_reference_is_acked(self):
        res = self._post(_order_token(merchantReference="999999"))
        self.assertEqual(res.status_code, 200)
        self.pi.refresh_from_db()
        self.assertEqual(self.pi.status, PaymentIntent.Status.REDIRECTED)

    def test_failed_status_marks_payment_failed(self):
        res = self._post(_order_token(
            merchantReference=str(self.order.id), paymentStatus="FAILED",
        ))
        self.assertEqual(res.status_code, 200)
        self.pi.refresh_from_db()
        self.assertEqual(self.pi.status, PaymentIntent.Status.FAILED)
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, Order.Status.PENDING_PAYMENT)


def _refund_token(**overrides) -> str:
    payload = {
        "refundUuid": "refund-uuid-1",
        "refundStatus": "SUCCESSFUL",
        "refundAmount": 24.20,
        "orderUuid": "m-uuid-123",
        "merchantReference": "1",
    }
    payload.update(overrides)
    return jwt.encode(payload, TEST_SECRET, algorithm="HS256")


@override_settings(MONTONIO_SECRET_KEY=TEST_SECRET, MONTONIO_ACCESS_KEY="ak")
class MontonioRefundWebhookTests(TestCase):
    def setUp(self):
        from checkout.models import Surcharge
        self.Surcharge = Surcharge
        self.user = User.objects.create_user(email="m@example.com", password="x")
        self.order = Order.objects.create(
            user=self.user,
            status=Order.Status.PAID,
            total_gross="24.20",
            currency="EUR",
            country_code="LT",
        )
        self.pi = PaymentIntent.objects.create(
            order=self.order,
            provider=PaymentIntent.Provider.MONTONIO,
            status=PaymentIntent.Status.SUCCEEDED,
            currency="EUR",
            amount_gross="24.20",
            external_id="m-uuid-123",
        )
        self.surcharge = Surcharge.objects.create(
            order=self.order,
            direction=Surcharge.Direction.REFUND,
            source=Surcharge.Source.AUTO,
            method=Surcharge.Method.MONTONIO,
            status=Surcharge.Status.PENDING,
            amount_gross="24.20",
            currency="EUR",
            external_id="refund-uuid-1",
        )

    def _post(self, token: str):
        return self.client.post(
            "/api/v1/payments/montonio/webhook",
            data=json.dumps({"refundToken": token}),
            content_type="application/json",
        )

    def test_successful_refund_resolves_surcharge(self):
        res = self._post(_refund_token())
        self.assertEqual(res.status_code, 200)
        self.surcharge.refresh_from_db()
        self.assertEqual(self.surcharge.status, self.Surcharge.Status.RESOLVED)
        self.assertIsNotNone(self.surcharge.paid_at)
        self.assertIn("refund_webhook", self.surcharge.raw_response)

    def test_rejected_refund_fails_surcharge(self):
        res = self._post(_refund_token(refundStatus="REJECTED"))
        self.assertEqual(res.status_code, 200)
        self.surcharge.refresh_from_db()
        self.assertEqual(self.surcharge.status, self.Surcharge.Status.FAILED)

    def test_canceled_refund_reopens_surcharge(self):
        res = self._post(_refund_token(refundStatus="CANCELED"))
        self.assertEqual(res.status_code, 200)
        self.surcharge.refresh_from_db()
        self.assertEqual(self.surcharge.status, self.Surcharge.Status.PENDING)
        self.assertEqual(self.surcharge.external_id, "")

    def test_webhook_idempotent(self):
        token = _refund_token()
        self._post(token)
        res = self._post(token)
        self.assertEqual(res.status_code, 200)
        self.surcharge.refresh_from_db()
        self.assertEqual(self.surcharge.status, self.Surcharge.Status.RESOLVED)

    def test_unknown_refund_creates_external_row(self):
        res = self._post(_refund_token(refundUuid="external-refund-9"))
        self.assertEqual(res.status_code, 200)
        s = self.Surcharge.objects.get(external_id="external-refund-9")
        self.assertEqual(s.status, self.Surcharge.Status.RESOLVED)
        self.assertEqual(s.order_id, self.order.id)
        self.assertEqual(str(s.amount_gross), "24.20")

    def test_unknown_refund_no_matching_order_acks(self):
        res = self._post(_refund_token(
            refundUuid="x", orderUuid="no-such-order"))
        self.assertEqual(res.status_code, 200)

    def test_invalid_refund_signature_rejected(self):
        bad = jwt.encode(
            {"refundUuid": "refund-uuid-1", "refundStatus": "SUCCESSFUL"},
            "wrong-secret", algorithm="HS256",
        )
        res = self._post(bad)
        self.assertEqual(res.status_code, 400)
        self.surcharge.refresh_from_db()
        self.assertEqual(self.surcharge.status, self.Surcharge.Status.PENDING)
