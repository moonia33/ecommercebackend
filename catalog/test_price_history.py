import datetime
from datetime import timedelta
from decimal import Decimal

from django.test import TestCase, override_settings
from django.utils import timezone

from api.models import Shop, ShopLanguage
from catalog.models import (
    Category,
    InventoryItem,
    Product,
    TaxClass,
    TaxRate,
    Variant,
    VariantPriceHistory,
    Warehouse,
)
from catalog.services.price_history import record_prices


@override_settings(ALLOWED_HOSTS=["*"], SHOP_CONTEXT_ENABLED=True, SHOP_CONTEXT_STRICT=True)
class PriceHistoryTests(TestCase):
    def setUp(self):
        self.shop = Shop.objects.create(
            code="s", name="S", primary_domain="ops.local", default_language_code="lt",
            default_country_code="LT", currency="EUR", is_active=True)
        ShopLanguage.objects.create(
            shop=self.shop, language_code="lt", is_active=True, is_default=True, sort_order=0)
        tax, _ = TaxClass.objects.get_or_create(code="standard", defaults={"name": "Standard"})
        TaxRate.objects.get_or_create(
            tax_class=tax, country_code="LT",
            defaults={"rate": "0.21", "valid_from": datetime.date(2020, 1, 1), "is_active": True})
        cat = Category.objects.create(name="Ritės", slug="rites-ph", is_active=True)
        self.product = Product.objects.create(
            sku="P-PH", name="Ritė", slug="rite-ph", tax_class=tax, category=cat, is_active=True)
        self.variant = Variant.objects.create(
            product=self.product, sku="V-PH", name="2500", price_eur="100.00", is_active=True)
        wh = Warehouse.objects.create(code="ph_wh", name="WH", kind="remote", country_code="LT", is_active=True)
        self.offer = InventoryItem.objects.create(variant=self.variant, warehouse=wh, qty_on_hand=5)
        self.t0 = timezone.now() - timedelta(days=10)

    def _record(self, at):
        return record_prices(shop_id=self.shop.id, channel="normal", now=at)

    def _open(self):
        return VariantPriceHistory.objects.get(variant=self.variant, channel="normal", valid_to__isnull=True)

    def test_first_record_has_no_prior_and_unchanged_price_adds_nothing(self):
        self._record(self.t0)
        self.assertIsNone(self._open().prior_unit_net)
        self.assertEqual(self._record(self.t0 + timedelta(hours=1))["changed"], 0)
        self.assertEqual(VariantPriceHistory.objects.filter(channel="normal").count(), 1)

    def test_discount_gets_prior_from_last_30_days(self):
        self._record(self.t0)
        self.offer.offer_discount_percent = 20
        self.offer.save()
        self._record(self.t0 + timedelta(days=1))
        row = self._open()
        self.assertEqual(row.unit_net, Decimal("80.00"))
        self.assertEqual(row.prior_unit_net, Decimal("100.00"))

    def test_prices_older_than_30_days_ignored(self):
        old = timezone.now() - timedelta(days=60)
        self._record(old)
        self.variant.price_eur = "120.00"
        self.variant.save()
        self._record(old + timedelta(days=1))  # 100 baigėsi prieš 59 d.
        self.offer.offer_discount_percent = 10
        self.offer.save()
        self._record(timezone.now())
        self.assertEqual(self._open().prior_unit_net, Decimal("120.00"))

    def test_api_enforced_compare_at_is_prior_price(self):
        self._record(self.t0)
        self.offer.offer_discount_percent = 20
        self.offer.save()
        self._record(self.t0 + timedelta(days=1))
        with override_settings(OMNIBUS_ENFORCE=True):
            res = self.client.get("/api/v1/catalog/products/rite-ph", HTTP_HOST="ops.local")
            self.assertEqual(res.status_code, 200, res.content[:300])
            v = res.json()["variants"][0]
            self.assertEqual(Decimal(v["compare_at_price"]["net"]), Decimal("100.00"))
            self.assertEqual(v["discount_percent"], 20)
            self.assertEqual(Decimal(v["prior_price"]["net"]), Decimal("100.00"))

            listing = self.client.get("/api/v1/catalog/products", HTTP_HOST="ops.local").json()["items"]
            item = next(i for i in listing if i["slug"] == "rite-ph")
            self.assertEqual(Decimal(item["compare_at_price"]["net"]), Decimal("100.00"))
            self.assertEqual(item["discount_percent"], 20)

    def test_enforced_without_history_hides_discount(self):
        self.offer.offer_discount_percent = 20
        self.offer.save()
        self._record(self.t0)  # pirmas įrašas jau su nuolaida – ankstesnės kainos nėra
        with override_settings(OMNIBUS_ENFORCE=True):
            v = self.client.get("/api/v1/catalog/products/rite-ph", HTTP_HOST="ops.local").json()["variants"][0]
        self.assertIsNone(v["compare_at_price"])
        self.assertIsNone(v["discount_percent"])
        # Be OMNIBUS_ENFORCE – kaip anksčiau (nuo list kainos).
        v = self.client.get("/api/v1/catalog/products/rite-ph", HTTP_HOST="ops.local").json()["variants"][0]
        self.assertEqual(v["discount_percent"], 20)

    def test_out_of_stock_keeps_offer_discount(self):
        self.offer.offer_discount_percent = 30
        self.offer.qty_on_hand = 0
        self.offer.save()
        self._record(self.t0)
        self.assertEqual(self._open().unit_net, Decimal("70.00"))
        v = self.client.get("/api/v1/catalog/products/rite-ph", HTTP_HOST="ops.local").json()["variants"][0]
        self.assertEqual(Decimal(v["price"]["net"]), Decimal("70.00"))
        self.assertEqual(v["discount_percent"], 30)
        listing = self.client.get("/api/v1/catalog/products", HTTP_HOST="ops.local").json()["items"]
        item = next(i for i in listing if i["slug"] == "rite-ph")
        self.assertEqual(Decimal(item["price"]["net"]), Decimal("70.00"))
        self.assertEqual(item["discount_percent"], 30)

    def test_in_stock_offer_wins_over_out_of_stock_discount(self):
        self.offer.offer_discount_percent = 30
        self.offer.qty_on_hand = 0
        self.offer.save()
        store = Warehouse.objects.create(code="ph_store", name="Store", kind="local", country_code="LT", is_active=True)
        InventoryItem.objects.create(variant=self.variant, warehouse=store, qty_on_hand=2)
        self._record(self.t0)
        self.assertEqual(self._open().unit_net, Decimal("100.00"))
        listing = self.client.get("/api/v1/catalog/products", HTTP_HOST="ops.local").json()["items"]
        item = next(i for i in listing if i["slug"] == "rite-ph")
        self.assertEqual(Decimal(item["price"]["net"]), Decimal("100.00"))

    def test_out_of_stock_listing_counts_variant_without_offer(self):
        self.offer.offer_discount_percent = 10
        self.offer.qty_on_hand = 0
        self.offer.save()
        Variant.objects.create(product=self.product, sku="V-PH2", name="1000", price_eur="60.00", is_active=True)
        listing = self.client.get("/api/v1/catalog/products", HTTP_HOST="ops.local").json()["items"]
        item = next(i for i in listing if i["slug"] == "rite-ph")
        self.assertEqual(Decimal(item["price"]["net"]), Decimal("60.00"))

    def test_listing_price_to_uses_discounted_variant_prices(self):
        self.offer.offer_discount_percent = 30
        self.offer.save()
        v2 = Variant.objects.create(product=self.product, sku="V-PH3", name="4000", price_eur="200.00", is_active=True)
        InventoryItem.objects.create(
            variant=v2, warehouse=self.offer.warehouse, qty_on_hand=1, offer_discount_percent=30)
        listing = self.client.get("/api/v1/catalog/products", HTTP_HOST="ops.local").json()["items"]
        item = next(i for i in listing if i["slug"] == "rite-ph")
        self.assertEqual(Decimal(item["price"]["net"]), Decimal("70.00"))
        self.assertEqual(Decimal(item["price_to"]["net"]), Decimal("140.00"))

    def test_price_sort_orders_sold_out_by_discounted_price(self):
        self.offer.qty_on_hand = 0
        self.offer.offer_discount_percent = 50
        self.offer.save()
        other = Product.objects.create(
            sku="P-PH2", name="Kita", slug="kita-ph", tax_class=self.product.tax_class,
            category=self.product.category, is_active=True)
        ov = Variant.objects.create(product=other, sku="V-K", name="1", price_eur="60.00", is_active=True)
        InventoryItem.objects.create(variant=ov, warehouse=self.offer.warehouse, qty_on_hand=0)
        listing = self.client.get("/api/v1/catalog/products?sort=price", HTTP_HOST="ops.local").json()["items"]
        slugs = [i["slug"] for i in listing if i["slug"] in {"rite-ph", "kita-ph"}]
        self.assertEqual(slugs, ["rite-ph", "kita-ph"])
