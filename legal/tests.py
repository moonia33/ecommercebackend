from __future__ import annotations

import json
from datetime import timedelta

from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings
from django.utils import timezone

import api.tests as api_tests
from checkout.models import Order, OrderConsent
from legal import services
from legal.models import (
    LegalAcceptance,
    LegalDocument,
    LegalDocumentVersion,
    LegalDocumentVersionTranslation,
)


def clear_legal():
    # Drop documents imported from seeded CMS pages by migration 0002.
    LegalDocumentVersionTranslation.objects.all().delete()
    LegalDocumentVersion.objects.all().delete()
    LegalDocument.objects.all().delete()


def make_version(doc, label, *, days_ago=1, reconsent=False, publish=True, body="Tekstas"):
    v = LegalDocumentVersion.objects.create(
        document=doc, version=label, requires_reconsent=reconsent,
        effective_from=timezone.now() - timedelta(days=days_ago))
    LegalDocumentVersionTranslation.objects.create(
        version=v, language_code="lt", title=f"{doc.get_kind_display()} {label}",
        body_markdown=body, change_summary=f"Pakeitimai {label}")
    if publish:
        v.publish()
    return v


@override_settings(ALLOWED_HOSTS=["*"], SHOP_CONTEXT_ENABLED=True, SHOP_CONTEXT_STRICT=True)
class LegalCheckoutTests(TestCase):
    setUp_base = api_tests.CheckoutProviderConfirmTests.setUp

    def setUp(self):
        self.setUp_base()
        clear_legal()
        self.terms = LegalDocument.objects.create(
            shop=self.shop, kind="terms", slug="taisykles", required_at_checkout=True)
        self.privacy = LegalDocument.objects.create(
            kind="privacy", slug="privatumas", required_at_checkout=True)
        self.t1 = make_version(self.terms, "2026-01-01", days_ago=30)
        self.p1 = make_version(self.privacy, "2026-01-01", days_ago=30)

    def _confirm(self, terms, privacy):
        return self.client.post(
            "/api/v1/checkout/confirm",
            data=json.dumps({
                "shipping_address_id": self.addr.id,
                "shipping_method": "store_pickup",
                "payment_method": "cod",
                "consents": [
                    {"kind": "terms", "document_version": terms},
                    {"kind": "privacy", "document_version": privacy},
                ],
            }),
            content_type="application/json", HTTP_HOST="ops.local")

    def test_consents_come_from_db(self):
        res = self.client.get("/api/v1/checkout/consents", HTTP_HOST="ops.local")
        self.assertEqual(res.status_code, 200, res.content)
        by_kind = {c["kind"]: c for c in res.json()}
        self.assertEqual(by_kind["terms"]["document_version"], "2026-01-01")
        self.assertEqual(by_kind["terms"]["url"], "/info/taisykles")
        self.assertEqual(by_kind["privacy"]["name"], "Privatumo politika 2026-01-01")

    def test_confirm_records_exact_versions(self):
        res = self._confirm("2026-01-01", "2026-01-01")
        self.assertEqual(res.status_code, 200, res.content[:400])
        order = Order.objects.get(user=self.user)
        oc = OrderConsent.objects.get(order=order, kind="terms")
        self.assertEqual(oc.legal_version, self.t1)
        acc = LegalAcceptance.objects.filter(user=self.user, order=order)
        self.assertEqual({a.version_id for a in acc}, {self.t1.id, self.p1.id})

    def test_stale_version_conflicts(self):
        make_version(self.terms, "2026-02-01", days_ago=1)
        res = self._confirm("2026-01-01", "2026-01-01")
        self.assertEqual(res.status_code, 409)

    def test_missing_consent_rejected(self):
        res = self.client.post(
            "/api/v1/checkout/confirm",
            data=json.dumps({
                "shipping_address_id": self.addr.id, "shipping_method": "store_pickup",
                "payment_method": "cod",
                "consents": [{"kind": "terms", "document_version": "2026-01-01"}],
            }),
            content_type="application/json", HTTP_HOST="ops.local")
        self.assertEqual(res.status_code, 400)

    def test_reconsent_flow(self):
        self.assertEqual(self._confirm("2026-01-01", "2026-01-01").status_code, 200)
        # Minor edit: no new consent requested.
        make_version(self.privacy, "2026-02-01", days_ago=2)
        me = self.client.get("/api/v1/auth/me", HTTP_HOST="ops.local").json()
        self.assertEqual(me["legal_pending"], [])
        # Material change.
        make_version(self.privacy, "2026-03-01", days_ago=1, reconsent=True)
        pending = self.client.get("/api/v1/legal/pending", HTTP_HOST="ops.local").json()
        self.assertEqual([p["version"] for p in pending], ["2026-03-01"])
        res = self.client.post(
            "/api/v1/legal/accept",
            data=json.dumps({"items": [{"kind": "privacy", "version": "2026-02-01"}]}),
            content_type="application/json", HTTP_HOST="ops.local")
        self.assertEqual(res.status_code, 409)
        res = self.client.post(
            "/api/v1/legal/accept",
            data=json.dumps({"items": [{"kind": "privacy", "version": "2026-03-01"}]}),
            content_type="application/json", HTTP_HOST="ops.local")
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual(res.json()["pending"], [])
        # History is kept: both acceptances remain.
        self.assertEqual(
            LegalAcceptance.objects.filter(user=self.user, version__document=self.privacy).count(), 2)

    def test_upcoming_version_not_current_yet(self):
        make_version(self.terms, "2099-01-01", days_ago=-30)
        defs = services.checkout_definitions(self.shop, "lt")
        self.assertEqual({d["kind"]: d["document_version"] for d in defs}["terms"], "2026-01-01")
        res = self.client.get("/api/v1/cms/pages/taisykles", HTTP_HOST="ops.local")
        legal = res.json()["legal"]
        self.assertEqual(legal["version"], "2026-01-01")
        self.assertEqual(legal["upcoming"]["version"], "2099-01-01")

    def test_page_history(self):
        make_version(self.terms, "2026-02-01", days_ago=1, body="Nauja")
        res = self.client.get("/api/v1/cms/pages/taisykles", HTTP_HOST="ops.local").json()
        self.assertEqual([v["version"] for v in res["legal"]["versions"]],
                         ["2026-02-01", "2026-01-01"])
        old = self.client.get(
            "/api/v1/cms/pages/taisykles?version=2026-01-01", HTTP_HOST="ops.local").json()
        self.assertFalse(old["legal"]["is_current"])
        self.assertEqual(old["content_blocks"][0]["payload"]["markdown"], "Tekstas")
        missing = self.client.get(
            "/api/v1/cms/pages/taisykles?version=nera", HTTP_HOST="ops.local")
        self.assertEqual(missing.status_code, 404)

    def test_shop_document_overrides_global(self):
        own = LegalDocument.objects.create(
            shop=self.shop, kind="privacy", slug="privatumas-s", required_at_checkout=True)
        make_version(own, "s-1")
        self.assertEqual(services.documents_for_shop(self.shop)["privacy"], own)
        self.assertEqual(services.documents_for_shop(None)["privacy"], self.privacy)


class LegalImmutabilityTests(TestCase):
    def setUp(self):
        clear_legal()
        self.doc = LegalDocument.objects.create(kind="terms", slug="taisykles")

    def test_effective_version_is_locked(self):
        v = make_version(self.doc, "", days_ago=1)
        self.assertTrue(v.version)
        v.requires_reconsent = True
        with self.assertRaises(ValidationError):
            v.save()
        t = v.translations.get()
        t.body_markdown = "kita"
        with self.assertRaises(ValidationError):
            t.save()
        with self.assertRaises(ValidationError):
            v.delete()

    def test_future_and_draft_versions_editable(self):
        v = make_version(self.doc, "future", days_ago=-5)
        v.requires_reconsent = True
        v.save()
        d = make_version(self.doc, "draft", publish=False)
        d.translations.update(body_markdown="x")
        d.delete()

    def test_publish_requires_text(self):
        v = LegalDocumentVersion.objects.create(document=self.doc, version="empty")
        with self.assertRaises(ValidationError):
            v.publish()

    def test_default_label_from_date(self):
        a = LegalDocumentVersion.objects.create(document=self.doc, effective_from=timezone.now())
        b = LegalDocumentVersion.objects.create(document=self.doc, effective_from=timezone.now())
        self.assertEqual(b.version, f"{a.version}-2")


class LegacyCheckoutModeTests(TestCase):
    def test_seeded_pages_imported_with_legacy_label(self):
        defs = {d["kind"]: d for d in services.checkout_definitions(None, "lt")}
        self.assertEqual(defs["terms"]["document_version"], "v1")
        self.assertEqual(defs["privacy"]["url"], "/info/privatumas")

    def test_no_documents_means_legacy(self):
        clear_legal()
        self.assertIsNone(services.checkout_definitions(None, "lt"))
        self.assertIsNone(services.validate_checkout_consents(None, "lt", {}))
