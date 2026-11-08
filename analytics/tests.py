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

    def test_accept_all_implies_every_category(self):
        cid = uuid.uuid4()
        self.client.post(
            "/api/v1/analytics/cookie-consent",
            data=json.dumps({"consent_id": str(cid), "version": 1, "action": "accept_all"}),
            content_type="application/json")
        row = CookieConsentRecord.objects.get(consent_id=cid)
        self.assertTrue(row.functional and row.analytics and row.marketing)

    def test_rate_limited_per_ip(self):
        from django.core.cache import cache

        from analytics.api import CONSENT_RATE_LIMIT

        cache.clear()
        body = json.dumps({"consent_id": str(uuid.uuid4()), "version": 1, "action": "reject_all"})
        codes = [
            self.client.post("/api/v1/analytics/cookie-consent", data=body,
                             content_type="application/json",
                             HTTP_CF_CONNECTING_IP="203.0.113.9").status_code
            for _ in range(CONSENT_RATE_LIMIT + 1)
        ]
        self.assertEqual(codes[-1], 429)
        self.assertTrue(all(c == 200 for c in codes[:-1]))
        other = self.client.post("/api/v1/analytics/cookie-consent", data=body,
                                 content_type="application/json",
                                 HTTP_CF_CONNECTING_IP="203.0.113.10")
        self.assertEqual(other.status_code, 200)

    def test_internal_proxy_address_not_rate_limited(self):
        from django.core.cache import cache

        from analytics.api import CONSENT_RATE_LIMIT

        cache.clear()
        body = json.dumps({"consent_id": str(uuid.uuid4()), "version": 1, "action": "reject_all"})
        for _ in range(CONSENT_RATE_LIMIT + 1):
            resp = self.client.post("/api/v1/analytics/cookie-consent", data=body,
                                    content_type="application/json", REMOTE_ADDR="10.0.1.7")
        self.assertEqual(resp.status_code, 200)

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
