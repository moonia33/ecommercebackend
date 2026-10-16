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
