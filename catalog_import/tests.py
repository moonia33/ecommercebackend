from __future__ import annotations

import json

from django.test import TestCase

from catalog.models import (
    Category,
    InventoryItem,
    Product,
    TaxClass,
    Variant,
    Warehouse,
)

from .models import ImportRun
from .services import create_run, execute_run


def _catalog_doc(items: list[dict], **kwargs) -> bytes:
    doc = {"format_version": 1, "source": "testsrc", "items": items}
    doc.update(kwargs)
    return json.dumps(doc).encode()


def _stock_doc(items: list[dict], warehouse_code: str = "main") -> bytes:
    return json.dumps(
        {"format_version": 1, "warehouse_code": warehouse_code, "items": items}
    ).encode()


class CatalogImportTestCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        TaxClass.objects.get_or_create(code="standard", defaults={"name": "Standard"})
        Category.objects.get_or_create(slug="cat-a", defaults={"name": "Cat A"})
        Warehouse.objects.get_or_create(
            code="main", defaults={"name": "Main", "country_code": "LT"}
        )

    def _run(self, items, options=None, **kwargs):
        run = create_run(
            kind=ImportRun.Kind.CATALOG,
            payload_bytes=_catalog_doc(items, **kwargs),
            options=options or {},
        )
        return execute_run(run)

    def test_product_with_variants(self):
        run = self._run([
            {
                "code": "xxxx",
                "product_group_code": "xxxx",
                "brand_code": "acme",
                "category": "cat-a",
                "tax_class": "standard",
                "price_net": "10.00",
                "translations": {"lt": {"name": "Preke A"}, "en": {"name": "Prod A"}},
                "variants": [
                    {"code": "xxxx-M", "product_group_code": "xxxx", "ean": "111",
                     "options": {"size": "m"}, "price_net": "10.00", "stock": {"main": 5}},
                    {"code": "xxxx-L", "product_group_code": "xxxx", "ean": "222",
                     "options": {"size": "l"}, "price_net": "12.50", "stock": {"main": 2}},
                ],
            }
        ])
        self.assertEqual(run.status, ImportRun.Status.DONE)
        product = Product.objects.get(sku="xxxx")
        self.assertEqual(product.product_group_code, "xxxx")
        self.assertEqual(product.import_source, "testsrc")
        self.assertEqual(product.category.slug, "cat-a")
        self.assertEqual(product.brand.slug, "acme")
        self.assertEqual(product.translations.get(language_code="en").name, "Prod A")

        variants = {v.sku: v for v in product.variants.all()}
        self.assertEqual(set(variants), {"xxxx-M", "xxxx-L"})
        self.assertEqual(variants["xxxx-M"].barcode, "111")
        self.assertEqual(variants["xxxx-M"].product_group_code, "xxxx")
        self.assertEqual(
            variants["xxxx-M"].option_values.get().option_value.code, "m"
        )
        self.assertEqual(
            InventoryItem.objects.get(variant=variants["xxxx-M"]).qty_on_hand, 5
        )

    def test_product_without_variants_gets_default(self):
        run = self._run([
            {
                "code": "solo",
                "price_net": "7.00",
                "tax_class": "standard",
                "ean": "999",
                "translations": {"lt": {"name": "Solo"}},
                "stock": {"main": 3},
            }
        ])
        self.assertEqual(run.status, ImportRun.Status.DONE)
        variant = Variant.objects.get(sku="solo")
        self.assertEqual(variant.barcode, "999")
        self.assertEqual(InventoryItem.objects.get(variant=variant).qty_on_hand, 3)

    def test_unknown_category_is_item_error_not_run_failure(self):
        run = self._run([
            {"code": "bad", "price_net": "1.00", "tax_class": "standard",
             "category": "nope", "translations": {"lt": {"name": "Bad"}}},
            {"code": "good", "price_net": "1.00", "tax_class": "standard",
             "translations": {"lt": {"name": "Good"}}},
        ])
        self.assertEqual(run.status, ImportRun.Status.DONE_WITH_ERRORS)
        self.assertEqual(run.failed_items, 1)
        self.assertEqual(run.ok_items, 1)
        self.assertFalse(Product.objects.filter(sku="bad").exists())
        self.assertTrue(Product.objects.filter(sku="good").exists())

    def test_missing_lt_name_is_item_error(self):
        run = self._run([
            {"code": "noname", "price_net": "1.00", "tax_class": "standard",
             "translations": {"en": {"name": "Only EN"}}},
        ])
        self.assertEqual(run.failed_items, 1)
        self.assertIn("translations.lt.name", run.items.first().error)

    def test_ean_conflict_is_item_error(self):
        Variant.objects.create(
            product=Product.objects.create(
                sku="other", name="Other", slug="other",
                tax_class=TaxClass.objects.get(code="standard"),
            ),
            sku="other-v", barcode="555", price_eur=1,
        )
        run = self._run([
            {"code": "dup", "price_net": "1.00", "tax_class": "standard",
             "translations": {"lt": {"name": "Dup"}},
             "variants": [{"code": "dup-v", "ean": "555"}]},
        ])
        self.assertEqual(run.failed_items, 1)
        self.assertIn("555", run.items.first().error)

    def test_upsert_is_idempotent(self):
        item = {"code": "upd", "price_net": "1.00", "tax_class": "standard",
                "translations": {"lt": {"name": "V1"}}}
        self._run([item])
        item["translations"]["lt"]["name"] = "V2"
        self._run([item])
        product = Product.objects.get(sku="upd")
        self.assertEqual(product.name, "V2")
        self.assertEqual(Product.objects.filter(sku="upd").count(), 1)
        self.assertEqual(Variant.objects.filter(sku="upd").count(), 1)

    def test_same_file_returns_existing_run(self):
        payload = _catalog_doc([])
        r1 = create_run(kind=ImportRun.Kind.CATALOG, payload_bytes=payload)
        execute_run(r1)
        r2 = create_run(kind=ImportRun.Kind.CATALOG, payload_bytes=payload)
        self.assertEqual(r1.id, r2.id)

    def test_dry_run_writes_nothing(self):
        run = self._run(
            [{"code": "dry", "price_net": "1.00", "tax_class": "standard",
              "translations": {"lt": {"name": "Dry"}}}],
            options={"dry_run": True},
        )
        self.assertEqual(run.status, ImportRun.Status.DONE)
        self.assertEqual(run.items.first().status, "skipped")
        self.assertFalse(Product.objects.filter(sku="dry").exists())

    def test_full_sync_deactivates_absent(self):
        self._run([
            {"code": "keep", "price_net": "1.00", "tax_class": "standard",
             "translations": {"lt": {"name": "Keep"}}},
        ])
        Product.objects.create(
            sku="stale", name="Stale", slug="stale", import_source="testsrc",
            tax_class=TaxClass.objects.get(code="standard"),
        )
        self._run(
            [{"code": "keep", "price_net": "1.00", "tax_class": "standard",
              "translations": {"lt": {"name": "Keep"}}}],
            mode="full_sync",
        )
        self.assertTrue(Product.objects.get(sku="keep").is_active)
        self.assertFalse(Product.objects.get(sku="stale").is_active)


class StockImportTestCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        tc, _ = TaxClass.objects.get_or_create(
            code="standard", defaults={"name": "Standard"}
        )
        cls.warehouse, _ = Warehouse.objects.get_or_create(
            code="main", defaults={"name": "Main", "country_code": "LT"}
        )
        product = Product.objects.create(
            sku="p1", name="P1", slug="p1", tax_class=tc
        )
        cls.variant = Variant.objects.create(
            product=product, sku="p1-v", price_eur=10
        )

    def _run_stock(self, items, warehouse_code="main", options=None):
        run = create_run(
            kind=ImportRun.Kind.STOCK,
            payload_bytes=_stock_doc(items, warehouse_code),
            options=options or {},
        )
        return execute_run(run)

    def test_stock_import(self):
        run = self._run_stock([{"code": "p1-v", "qty": 7}])
        self.assertEqual(run.status, ImportRun.Status.DONE)
        self.assertEqual(
            InventoryItem.objects.get(
                variant=self.variant, warehouse=self.warehouse
            ).qty_on_hand,
            7,
        )

    def test_stock_unknown_sku_and_negative_qty(self):
        run = self._run_stock([
            {"code": "missing", "qty": 1},
            {"code": "p1-v", "qty": -5},
            {"code": "p1-v", "qty": 4},
        ])
        self.assertEqual(run.failed_items, 2)
        self.assertEqual(run.ok_items, 1)

    def test_stock_dry_run(self):
        InventoryItem.objects.create(
            variant=self.variant, warehouse=self.warehouse, qty_on_hand=2
        )
        run = self._run_stock(
            [{"code": "p1-v", "qty": 50}], options={"dry_run": True}
        )
        self.assertEqual(run.status, ImportRun.Status.DONE)
        self.assertEqual(
            InventoryItem.objects.get(variant=self.variant).qty_on_hand, 2
        )
