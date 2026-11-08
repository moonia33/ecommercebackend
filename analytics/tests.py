from __future__ import annotations

import json
import uuid

from django.test import TestCase, override_settings

from analytics.models import CookieConsentRecord
from api.models import Shop, ShopConfig


@override_settings(ALLOWED_HOSTS=["*"])
class CookieConsentTests(TestCase):
    def test_records_choice(self):
        cid = uuid.uuid4()
        for action, marketing in (("custom", False), ("accept_all", True)):
            res = self.client.post(
                "/api/v1/analytics/cookie-consent",
                data=json.dumps({"consent_id": str(cid), "version": 2, "action": action,
                                 "analytics": True, "marketing": marketing}),
                content_type="application/json", HTTP_USER_AGENT="UA")
            self.assertEqual(res.status_code, 200, res.content)
        rows = list(CookieConsentRecord.objects.filter(consent_id=cid).order_by("id"))
        self.assertEqual([(r.action, r.marketing) for r in rows],
                         [("custom", False), ("accept_all", True)])
        self.assertEqual(rows[0].user_agent, "UA")

    def test_rejects_bad_id(self):
        res = self.client.post(
            "/api/v1/analytics/cookie-consent",
            data=json.dumps({"consent_id": "x", "version": 1, "action": "custom"}),
            content_type="application/json")
        self.assertEqual(res.status_code, 422)


@override_settings(ALLOWED_HOSTS=["*"], SHOP_CONTEXT_ENABLED=True)
class ShopConfigTrackingTests(TestCase):
    def test_tracking_ids_exposed(self):
        shop = Shop.objects.create(code="t", name="T", primary_domain="t.local")
        ShopConfig.objects.create(shop=shop, ga4_measurement_id="G-ABC123",
                                  meta_pixel_id="123", cookie_consent_version=3)
        data = self.client.get("/api/v1/shop/config", HTTP_HOST="t.local").json()
        self.assertEqual(data["ga4_measurement_id"], "G-ABC123")
        self.assertEqual(data["meta_pixel_id"], "123")
        self.assertEqual(data["cookie_consent_version"], 3)
