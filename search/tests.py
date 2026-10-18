from __future__ import annotations

from unittest import mock

from django.test import Client, TestCase, override_settings

from api.models import Shop, ShopLanguage
from catalog.models import (
    Category,
    Feature,
    FeatureValue,
    InventoryItem,
    Product,
    ProductFeatureValue,
    TaxClass,
    TaxRate,
    Variant,
    Warehouse,
)
from search.services import (
    full_reindex,
    product_document,
    search_product_ids,
)


class FakeDocuments:
    def __init__(self, result=None, error=None):
        self.result = result or {"hits": []}
        self.error = error
        self.last_search_params = None
        self.imported_docs = None

    def search(self, params):
        self.last_search_params = params
        if self.error:
            raise self.error
        return self.result

    def import_(self, docs, opts):
        self.imported_docs = list(docs)
        return [{"success": True} for _ in docs]


class _NamedResourceStore:
    """Mimics client.collections[c].{synonyms,overrides} API surface."""

    def __init__(self):
        self.items: dict[str, dict] = {}
        self.deleted: list[str] = []

    def retrieve(self):
        return {"items": list(self.items.values())}  # overridden per-use

    def upsert(self, item_id, body):
        self.items[item_id] = {"id": item_id, **body}
        return body

    def __getitem__(self, item_id):
        store = self

        class _Handle:
            def delete(self):
                store.items.pop(item_id, None)
                store.deleted.append(item_id)

        return _Handle()


class _SynonymsStore(_NamedResourceStore):
    def retrieve(self):
        return {"synonyms": list(self.items.values())}


class _OverridesStore(_NamedResourceStore):
    def retrieve(self):
        return {"overrides": list(self.items.values())}


class _Collections(dict):
    """Dict with the .create() method the real client exposes."""

    def create(self, schema):
        self[schema["name"]] = FakeCollection(FakeDocuments(), schema)


class FakeCollection:
    def __init__(self, documents, schema=None):
        self.documents = documents
        self.synonyms = _SynonymsStore()
        self.overrides = _OverridesStore()
        self.schema = schema
        self.deleted = False

    def retrieve(self):
        from search.services import SCHEMA

        return {"name": "products", "fields": (self.schema or SCHEMA)["fields"]}

    def delete(self):
        self.deleted = True


class FakeClient:
    def __init__(self, hits=None, error=None):
        self.documents = FakeDocuments(
            result={"hits": hits or []}, error=error)
        self.collections = _Collections()
        self.collections["products"] = FakeCollection(self.documents)


def _hits(*ids):
    return [{"document": {"id": str(i)}} for i in ids]


@override_settings(
    ALLOWED_HOSTS=["*"], SHOP_CONTEXT_ENABLED=True, SHOP_CONTEXT_STRICT=True)
class SearchTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.shop = Shop.objects.create(
            code="srch", name="Search", primary_domain="srch.local",
            default_language_code="lt", default_country_code="LT",
            currency="EUR", is_active=True)
        ShopLanguage.objects.create(
            shop=self.shop, language_code="lt", is_active=True,
            is_default=True, sort_order=0)

        self.tax = TaxClass.objects.create(code="std-s", name="Std")
        import datetime
        TaxRate.objects.create(
            tax_class=self.tax, country_code="LT", rate="0.21",
            valid_from=datetime.date(2020, 1, 1), is_active=True)
        self.wh = Warehouse.objects.create(
            code="m", name="Main", country_code="LT",
            kind=Warehouse.Kind.LOCAL, is_active=True)

        self.parent_cat = Category.objects.create(
            name="Rods", slug="rods-s", is_active=True)
        self.cat = Category.objects.create(
            name="Carp rods", slug="carp-s", is_active=True)

        self.feature = Feature.objects.create(
            code="technique", name="Žūklės rūšis")
        self.fv = FeatureValue.objects.create(
            feature=self.feature, value="Karpiams")

    def _product(self, sku, name, *, category=None, stock=5):
        p = Product.objects.create(
            sku=sku, name=name, slug=f"s-{sku.lower()}",
            tax_class=self.tax, category=category, is_active=True)
        v = Variant.objects.create(
            product=p, sku=f"V-{sku}", name="v",
            price_eur="10.00", is_active=True)
        if stock:
            InventoryItem.objects.create(
                variant=v, warehouse=self.wh, qty_on_hand=stock)
        return p

    def test_document_fields(self):
        p = self._product("SLA-5500", "Meškerė SLA-5500",
                          category=self.cat)
        ProductFeatureValue.objects.create(
            product=p, feature=self.feature, feature_value=self.fv)

        doc = product_document(p)
        self.assertEqual(doc["id"], str(p.id))
        self.assertEqual(doc["shop_ids"], [self.shop.id])
        self.assertTrue(doc["vis_normal"])
        self.assertFalse(doc["vis_outlet"])
        self.assertEqual(doc["price_normal"], 10.0)
        self.assertIn("technique:Karpiams", doc["feature_facets"])
        self.assertIn("Meškerė", doc["search_text"])
        # diacritic-folded copy present for LT fallback
        self.assertIn("meskere", doc["search_text"].lower())

    def test_document_excludes_inactive_and_no_offer_visibility(self):
        p = self._product("X1", "Hidden", stock=0)
        doc = product_document(p)
        self.assertFalse(doc["vis_normal"])
        self.assertIsNone(doc["price_normal"])

        p.is_active = False
        p.save()
        self.assertIsNone(product_document(p))

    def test_search_ids_order_and_filters(self):
        client = FakeClient(hits=_hits(9, 3, 5))
        with mock.patch(
            "search.services.get_client", return_value=client
        ):
            ids = search_product_ids(
                q="meškerė", shop_id=self.shop.id, channel="normal")
        self.assertEqual(ids, [9, 3, 5])
        params = client.documents.last_search_params
        self.assertIn("shop_ids:=[" + str(self.shop.id) + "]", params["filter_by"])
        self.assertIn("vis_normal:=true", params["filter_by"])
        self.assertEqual(params["infix"], "fallback")

    def test_search_unavailable_returns_none(self):
        client = FakeClient(error=RuntimeError("down"))
        with mock.patch(
            "search.services.get_client", return_value=client
        ):
            self.assertIsNone(search_product_ids(
                q="x", shop_id=None, channel="normal"))

        with mock.patch(
            "search.services.get_client", return_value=None
        ):
            self.assertIsNone(search_product_ids(
                q="x", shop_id=None, channel="normal"))

    def test_products_endpoint_uses_engine_order(self):
        a = self._product("A1", "Zeta rod", category=self.cat)
        b = self._product("B1", "Alpha rod", category=self.cat)
        c = self._product("C1", "Unrelated", category=self.cat)

        client = FakeClient(hits=_hits(b.id, a.id))
        with mock.patch(
            "search.services.get_client", return_value=client
        ):
            res = self.client.get(
                "/api/v1/catalog/products?q=rod",
                HTTP_HOST="srch.local")
        self.assertEqual(res.status_code, 200)
        slugs = [p["slug"] for p in res.json()["items"]]
        self.assertEqual(slugs, [b.slug, a.slug])

    def test_products_endpoint_falls_back_to_orm(self):
        p = self._product("FB1", "Fallback meškerė", category=self.cat)
        client = FakeClient(error=RuntimeError("down"))
        with mock.patch(
            "search.services.get_client", return_value=client
        ):
            res = self.client.get(
                "/api/v1/catalog/products?q=fallback",
                HTTP_HOST="srch.local")
        self.assertEqual(res.status_code, 200)
        slugs = [x["slug"] for x in res.json()["items"]]
        self.assertIn(p.slug, slugs)

    def test_empty_hits_return_empty_list(self):
        self._product("ZZ", "Something", category=self.cat)
        client = FakeClient(hits=[])
        with mock.patch(
            "search.services.get_client", return_value=client
        ):
            res = self.client.get(
                "/api/v1/catalog/products?q=nomatch",
                HTTP_HOST="srch.local")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()["items"], [])

    def test_full_reindex_imports_docs(self):
        self._product("R1", "Rod one", category=self.cat)
        self._product("R2", "Rod two", category=self.cat)
        client = FakeClient()
        with mock.patch(
            "search.services.get_client", return_value=client
        ):
            out = full_reindex()
        self.assertEqual(out["indexed"], 2)
        self.assertEqual(len(client.documents.imported_docs), 2)


@override_settings(ALLOWED_HOSTS=["*"])
class CurationSyncTests(TestCase):
    def setUp(self):
        self.client = FakeClient()
        self._patcher = mock.patch(
            "search.services.get_client", return_value=self.client)
        self._patcher.start()

    def tearDown(self):
        self._patcher.stop()

    def _product(self, sku):
        tax, _ = TaxClass.objects.get_or_create(code="t", name="T")
        return Product.objects.create(
            sku=sku, name=sku, slug=f"c-{sku.lower()}",
            tax_class=tax, is_active=True)

    def test_synonym_upsert_and_cleanup(self):
        from search.models import SearchSynonym
        from search.services import sync_synonyms

        syn = SearchSynonym.objects.create(
            name="rods", terms="meškerė, spiningas, meškere")
        coll = self.client.collections["products"]
        coll.synonyms.items["syn-999"] = {"id": "syn-999"}
        coll.synonyms.items["ext-1"] = {"id": "ext-1"}  # unmanaged: keep

        out = sync_synonyms()
        self.assertEqual(out["synced"], 1)
        self.assertIn(f"syn-{syn.id}", coll.synonyms.items)
        self.assertNotIn("syn-999", coll.synonyms.items)
        self.assertIn("ext-1", coll.synonyms.items)

    def test_synonym_root_becomes_multiway_member(self):
        from search.models import SearchSynonym
        from search.services import sync_synonyms

        syn = SearchSynonym.objects.create(
            name="rooted", root="meškerė", terms="feederis, pickeris")
        sync_synonyms()
        body = self.client.collections["products"].synonyms.items[
            f"syn-{syn.id}"]
        # Root is pushed as a multi-way member — Typesense `root` synonyms
        # only remap at index time and would not expand queries.
        self.assertNotIn("root", body)
        self.assertEqual(body["synonyms"], ["feederis", "pickeris", "meškerė"])

    def test_override_pins_and_excludes(self):
        from search.models import (
            SearchOverride, SearchOverrideExclude, SearchOverridePin)
        from search.services import sync_overrides

        a = self._product("PA"); b = self._product("PB")
        o = SearchOverride.objects.create(
            name="sale", query="meškerė", match="exact")
        SearchOverridePin.objects.create(override=o, product=a, position=1)
        SearchOverrideExclude.objects.create(override=o, product=b)

        out = sync_overrides()
        self.assertEqual(out["synced"], 1)
        body = self.client.collections["products"].overrides.items[
            f"ovr-{o.id}"]
        self.assertEqual(body["rule"], {"q": "meškerė", "match": "exact"})
        self.assertEqual(
            body["includes"], [{"id": str(a.id), "position": 1}])
        self.assertEqual(
            body["excludes"], [{"id": str(b.id)}])

    def test_expired_override_not_pushed(self):
        from datetime import timedelta
        from django.utils import timezone as tz
        from search.models import SearchOverride, SearchOverridePin
        from search.services import sync_overrides

        a = self._product("PE")
        o = SearchOverride.objects.create(
            name="old", query="x", ends_at=tz.now() - timedelta(days=1))
        SearchOverridePin.objects.create(override=o, product=a, position=1)

        out = sync_overrides()
        self.assertEqual(out["synced"], 0)
        self.assertEqual(
            self.client.collections["products"].overrides.items, {})

    def test_inactive_synonym_removed_from_engine(self):
        from search.models import SearchSynonym
        from search.services import sync_synonyms

        syn = SearchSynonym.objects.create(name="s1", terms="a, b")
        sync_synonyms()
        self.assertIn(
            f"syn-{syn.id}",
            self.client.collections["products"].synonyms.items)

        syn.is_active = False
        syn.save()
        sync_synonyms()
        self.assertNotIn(
            f"syn-{syn.id}",
            self.client.collections["products"].synonyms.items)
