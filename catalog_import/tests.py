from __future__ import annotations

import json

from django.test import TestCase

from catalog.models import (
    Category,
    InventoryItem,
    OptionValue,
    Product,
    ProductOptionType,
    TaxClass,
    Variant,
    VariantOptionValue,
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

    def test_variant_pack_fields_are_imported(self):
        run = self._run([
            {
                "code": "boxitem",
                "tax_class": "standard",
                "price_net": "10.00",
                "translations": {"lt": {"name": "Dežė"}},
                "variants": [
                    {
                        "code": "boxitem-1",
                        "price_net": "120.00",
                        "pack_unit": "pcs",
                        "pack_quantity": 6,
                        "trade_pack_qty": 60,
                        "pack_barcode": "5056212116980",
                        "sold_as_pack": True,
                        "pack_price": "120.00",
                        "pack_purchase_price": "84.00",
                    },
                ],
            }
        ])
        self.assertEqual(run.status, ImportRun.Status.DONE)
        v = Variant.objects.get(sku="boxitem-1")
        self.assertTrue(v.sold_as_pack)
        self.assertEqual(v.pack_unit, "pcs")
        self.assertEqual(v.pack_quantity, 6)
        self.assertEqual(v.trade_pack_qty, 60)
        self.assertEqual(v.pack_barcode, "5056212116980")
        self.assertEqual(str(v.pack_price_eur), "120.00")
        self.assertEqual(str(v.pack_purchase_price_eur), "84.00")

    def test_feature_names_and_labels_are_humanized(self):
        run = self._run([
            {
                "code": "chair",
                "tax_class": "standard",
                "price_net": "50.00",
                "translations": {"lt": {"name": "Kėdė"}},
                "features": [
                    {"code": "material_fabric", "value": "100-polyester"},
                    {"code": "cast-range", "value": "10-30"},
                ],
            }
        ])
        self.assertEqual(run.status, ImportRun.Status.DONE)
        product = Product.objects.get(sku="chair")
        rows = {
            r.feature.code: r.feature_value
            for r in product.feature_values.select_related(
                "feature", "feature_value"
            )
        }
        # Kodai normalizuojami ('_' -> '-'), slugai lieka 'value',
        # bet 'label'/'name' yra žmogui skaitomi.
        self.assertEqual(rows["material-fabric"].feature.name, "Material Fabric")
        self.assertEqual(rows["material-fabric"].label, "100 polyester")
        # Skaičius-skaičius brūkšnys išlieka (diapazonas).
        self.assertEqual(rows["cast-range"].label, "10-30")

    def test_feature_label_prefers_en_translation(self):
        run = self._run([
            {
                "code": "net",
                "tax_class": "standard",
                "price_net": "20.00",
                "translations": {"lt": {"name": "Graibštas"}},
                "features": [
                    {
                        "code": "length",
                        "value": "2-5-m",
                        "name_translations": {"en": "Handle Length"},
                        "value_translations": {"en": "2.5 m"},
                    }
                ],
            }
        ])
        self.assertEqual(run.status, ImportRun.Status.DONE)
        fv = Product.objects.get(sku="net").feature_values.get().feature_value
        self.assertEqual(fv.label, "2.5 m")
        self.assertEqual(fv.feature.name, "Handle Length")

    def test_variant_features_are_imported_per_variant(self):
        run = self._run([
            {
                "code": "rod",
                "tax_class": "standard",
                "price_net": "100.00",
                "translations": {"lt": {"name": "Meškerė"}},
                "features": [{"code": "material", "value": "carbon"}],
                "variants": [
                    {
                        "code": "rod-175",
                        "options": {"size": "1.75m"},
                        "price_net": "100.00",
                        "features": [{"code": "length", "value": "175cm"}],
                    },
                    {
                        "code": "rod-195",
                        "options": {"size": "1.95m"},
                        "price_net": "110.00",
                        "features": [{"code": "length", "value": "195cm"}],
                    },
                ],
            }
        ])
        self.assertEqual(run.status, ImportRun.Status.DONE)
        product = Product.objects.get(sku="rod")
        self.assertEqual(product.feature_values.get().feature.code, "material")
        variants = {v.sku: v for v in product.variants.all()}
        v175 = variants["rod-175"].feature_values.get()
        self.assertEqual(v175.feature.code, "length")
        self.assertEqual(v175.feature_value.value, "175cm")
        self.assertEqual(
            variants["rod-195"].feature_values.get().feature_value.value, "195cm"
        )

    def test_variant_images_are_attached_to_specific_variant(self):
        run = self._run([
            {
                "code": "shirt",
                "tax_class": "standard",
                "price_net": "15.00",
                "translations": {"lt": {"name": "Marškiniai"}},
                "images": [{"url": "https://cdn.x/shared.jpg", "sort_order": 0}],
                "variants": [
                    {"code": "shirt-red", "options": {"color": "red"},
                     "images": [{"url": "https://cdn.x/red.jpg", "sort_order": 1}]},
                    {"code": "shirt-blue", "options": {"color": "blue"},
                     "images": [{"url": "https://cdn.x/blue.jpg", "sort_order": 1}]},
                ],
            }
        ])
        self.assertEqual(run.status, ImportRun.Status.DONE)
        product = Product.objects.get(sku="shirt")
        variants = {v.sku: v for v in product.variants.all()}

        shared = product.images.get(image_url="https://cdn.x/shared.jpg")
        self.assertEqual(shared.variants.count(), 0)

        red_img = product.images.get(image_url="https://cdn.x/red.jpg")
        blue_img = product.images.get(image_url="https://cdn.x/blue.jpg")
        self.assertEqual(
            list(red_img.variants.values_list("id", flat=True)),
            [variants["shirt-red"].id],
        )
        self.assertEqual(
            list(blue_img.variants.values_list("id", flat=True)),
            [variants["shirt-blue"].id],
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


from unittest.mock import MagicMock, patch

from catalog.models import ProductImage

from . import services as import_services


class SearchSyncTestCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        TaxClass.objects.get_or_create(code="standard", defaults={"name": "S"})
        Warehouse.objects.get_or_create(
            code="main", defaults={"name": "Main", "country_code": "LT"}
        )

    def _catalog_run(self, options=None):
        doc = _catalog_doc([{
            "code": "sync-p", "tax_class": "standard", "price_net": "1.00",
            "translations": {"lt": {"name": "P"}},
        }])
        return create_run(kind="catalog", payload_bytes=doc, options=options or {})

    def test_signal_sync_suppressed_during_execute_run(self):
        from search.signals import search_sync_suppressed

        seen = []
        real_apply = import_services._apply_product

        def spy(run, item, seen_ids, row):
            seen.append(search_sync_suppressed())
            return real_apply(run, item, seen_ids, row)

        with patch.object(import_services, "_apply_product", side_effect=spy):
            run = execute_run(self._catalog_run())
        self.assertEqual(run.status, ImportRun.Status.DONE)
        self.assertEqual(seen, [True])
        self.assertFalse(search_sync_suppressed())

    @patch("search.services.search_enabled", return_value=True)
    def test_schedule_search_sync_registers_on_commit(self, _enabled):
        with patch.object(
            import_services.transaction, "on_commit"
        ) as mock_on_commit:
            run = execute_run(self._catalog_run())
        self.assertEqual(run.status, ImportRun.Status.DONE)
        product = Product.objects.get(sku="sync-p")
        mock_on_commit.assert_called_once()
        dispatch = mock_on_commit.call_args[0][0]
        chunks = dispatch.__defaults__[0] if dispatch.__defaults__ else None
        self.assertEqual(chunks, [[product.id]])

    @patch("search.services.search_enabled", return_value=True)
    def test_no_search_sync_on_dry_run(self, _enabled):
        with patch.object(
            import_services.transaction, "on_commit"
        ) as mock_on_commit:
            run = execute_run(self._catalog_run(options={"dry_run": True}))
        self.assertEqual(run.status, ImportRun.Status.DONE)
        mock_on_commit.assert_not_called()


class AsyncApplyTestCase(TestCase):
    def test_start_async_apply_starts_thread_when_eager(self):
        run = create_run(
            kind="catalog",
            payload_bytes=_catalog_doc([]),
        )
        with (
            patch.object(
                import_services.settings, "CELERY_TASK_ALWAYS_EAGER", True
            ),
            patch.object(import_services.threading, "Thread") as mock_thread,
        ):
            import_services._start_async_apply(run)
        mock_thread.assert_called_once()
        self.assertTrue(mock_thread.call_args.kwargs["daemon"])
        mock_thread.return_value.start.assert_called_once_with()
        self.assertEqual(run.status, ImportRun.Status.QUEUED)


class ImageIngestTestCase(TestCase):
    def setUp(self):
        TaxClass.objects.get_or_create(code="standard", defaults={"name": "S"})
        self.product = Product.objects.create(
            sku="img-p", name="Img P", slug="img-p", is_active=True,
            tax_class=TaxClass.objects.get(code="standard"),
        )

    def _fake_response(self, body: bytes, content_type: str = "image/jpeg"):
        resp = MagicMock()
        resp.headers = {"Content-Type": content_type, "Content-Length": str(len(body))}
        resp.read.side_effect = lambda n=-1: body[:n] if n >= 0 else body
        resp.__enter__ = lambda s: resp
        resp.__exit__ = lambda s, *a: False
        return resp

    def _run_import_with_image(self):
        run = _run = None
        doc = _catalog_doc([{
            "code": "img-p", "tax_class": "standard", "price_net": "5.00",
            "translations": {"lt": {"name": "Img"}},
            "images": [{"url": "https://cdn.x/pic.jpg"}],
        }])
        r = create_run(kind="catalog", payload_bytes=doc, file_name="t.json")
        execute_run(r)
        return r

    def test_import_marks_image_pending_ingest(self):
        self._run_import_with_image()
        img = ProductImage.objects.get(product=self.product)
        self.assertEqual(img.ingest_status, ProductImage.INGEST_PENDING)

    @patch("urllib.request.urlopen")
    def test_ingest_downloads_and_marks_done(self, mock_open):
        self._run_import_with_image()
        img = ProductImage.objects.get(product=self.product)
        mock_open.return_value = self._fake_response(b"\xff\xd8\xff" + b"x" * 5000)

        from catalog.tasks import ingest_product_image_task
        ingest_product_image_task(img.pk)

        img.refresh_from_db()
        self.assertEqual(img.ingest_status, ProductImage.INGEST_DONE)
        self.assertTrue(bool(img.image))
        self.assertEqual(img.ingest_error, "")
        img.image.delete(save=False)

    @patch("urllib.request.urlopen")
    def test_ingest_rejects_bad_content_type(self, mock_open):
        self._run_import_with_image()
        img = ProductImage.objects.get(product=self.product)
        mock_open.return_value = self._fake_response(b"<html>x" * 100, "text/html")

        from catalog.tasks import ingest_product_image_task
        ingest_product_image_task(img.pk)

        img.refresh_from_db()
        self.assertEqual(img.ingest_status, ProductImage.INGEST_ERROR)
        self.assertIn("content-type", img.ingest_error)
        self.assertFalse(bool(img.image))

    @patch("urllib.request.urlopen")
    def test_ingest_rejects_oversized(self, mock_open):
        self._run_import_with_image()
        img = ProductImage.objects.get(product=self.product)
        big = b"x" * (9 * 1024 * 1024)
        mock_open.return_value = self._fake_response(big)

        from catalog.tasks import ingest_product_image_task
        ingest_product_image_task(img.pk)

        img.refresh_from_db()
        self.assertEqual(img.ingest_status, ProductImage.INGEST_ERROR)
        self.assertIn("large", img.ingest_error)


class AttributeNormalizationImportTests(CatalogImportTestCase):
    """format_version=2: normalized numeric/datatype/unit fields persist."""

    def test_feature_numeric_fields_persist(self):
        run = self._run(
            [
                {
                    "code": "rod1",
                    "tax_class": "standard",
                    "price_net": "10.00",
                    "translations": {"lt": {"name": "Meškerė"}},
                    "features": [
                        {
                            "code": "line-strength",
                            "value": "9-07-kg",
                            "value_num": "9.07",
                            "unit": "kg",
                            "datatype": "number",
                            "value_translations": {"en": "9,07 kg (20 lb)"},
                        },
                        {
                            "code": "casting-weight",
                            "value": "10-40-g",
                            "value_num": "10",
                            "value_num_max": "40",
                            "unit": "g",
                            "datatype": "range",
                        },
                    ],
                    "variants": [
                        {
                            "code": "rod1-a",
                            "name": "2.70m / 10-40g",
                            "options": {"casting-weight": "10-40-g"},
                            "price_net": "10.00",
                        }
                    ],
                    "option_value_meta": {
                        "casting-weight": {
                            "10-40-g": {"num": "10", "num_max": "40", "unit": "g"}
                        }
                    },
                }
            ]
        )
        self.assertEqual(run.status, ImportRun.Status.DONE)
        product = Product.objects.get(sku="rod1")

        strength = product.feature_values.get(feature__code="line-strength")
        self.assertEqual(str(strength.feature_value.value_num), "9.0700")
        self.assertEqual(strength.feature_value.unit, "kg")
        self.assertEqual(strength.feature.datatype, "number")
        self.assertEqual(strength.feature.unit, "kg")

        cast = product.feature_values.get(feature__code="casting-weight")
        self.assertEqual(str(cast.feature_value.value_num), "10.0000")
        self.assertEqual(str(cast.feature_value.value_num_max), "40.0000")
        self.assertEqual(cast.feature.datatype, "range")

        variant = Variant.objects.get(sku="rod1-a")
        self.assertEqual(variant.name, "2.70m / 10-40g")
        opt_value = variant.option_values.get().option_value
        self.assertEqual(str(opt_value.value_num), "10.0000")
        self.assertEqual(str(opt_value.value_num_max), "40.0000")
        self.assertEqual(opt_value.unit, "g")
        self.assertEqual(opt_value.option_type.unit, "g")

    def test_empty_variant_name_keeps_existing(self):
        self._run(
            [
                {
                    "code": "rod2",
                    "tax_class": "standard",
                    "price_net": "10.00",
                    "translations": {"lt": {"name": "Meškerė"}},
                    "variants": [
                        {"code": "rod2-a", "name": "Old name", "price_net": "10.00"}
                    ],
                }
            ]
        )
        self.assertEqual(Variant.objects.get(sku="rod2-a").name, "Old name")
        self._run(
            [
                {
                    "code": "rod2",
                    "tax_class": "standard",
                    "price_net": "10.00",
                    "translations": {"lt": {"name": "Meškerė"}},
                    "variants": [{"code": "rod2-a", "name": "", "price_net": "10.00"}],
                }
            ]
        )
        self.assertEqual(Variant.objects.get(sku="rod2-a").name, "Old name")

    def test_v1_payload_still_accepted(self):
        run = self._run(
            [
                {
                    "code": "v1item",
                    "tax_class": "standard",
                    "price_net": "1.00",
                    "translations": {"lt": {"name": "V1"}},
                }
            ]
        )
        self.assertEqual(run.status, ImportRun.Status.DONE)


class OptionCleanupTests(CatalogImportTestCase):
    """Kai vėlesnis importas ateina be options (n -> 0) – senos eilutės
    ProductOptionType/VariantOptionValue valomos."""

    def _item(self, options):
        return {
            "code": "rodz",
            "tax_class": "standard",
            "price_net": "10.00",
            "translations": {"lt": {"name": "Meškerė"}},
            "variants": [
                {"code": "rodz-a", "options": options, "price_net": "10.00"}
            ],
        }

    def test_options_cleared_on_reimport(self):
        run = self._run([self._item({"casting-weight": "10-40-g"})])
        self.assertEqual(run.status, ImportRun.Status.DONE)
        variant = Variant.objects.get(sku="rodz-a")
        self.assertEqual(variant.option_values.count(), 1)
        self.assertEqual(
            ProductOptionType.objects.filter(product=variant.product).count(), 1
        )
        self._run([self._item({})])
        variant.refresh_from_db()
        self.assertEqual(variant.option_values.count(), 0)
        self.assertEqual(
            ProductOptionType.objects.filter(product=variant.product).count(), 0
        )
