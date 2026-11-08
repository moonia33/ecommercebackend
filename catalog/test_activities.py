import datetime
from io import StringIO

from django.test import TestCase, override_settings

from catalog.activities import activity_product_counts, apply_activity_rules
from catalog.models import (
    Activity,
    ActivityRule,
    Brand,
    Category,
    Product,
    ProductActivity,
    TaxClass,
    TaxRate,
    Variant,
)
from search.services import product_document


@override_settings(CACHES={"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}})
class ActivityTests(TestCase):
    def setUp(self):
        self.tax, _ = TaxClass.objects.get_or_create(code="standard", defaults={"name": "Standard"})
        TaxRate.objects.create(
            tax_class=self.tax, country_code="LT", rate="0.21",
            valid_from=datetime.date(2020, 1, 1), is_active=True,
        )
        self.root_cat = Category.objects.create(name="Įranga", slug="iranga")
        self.carp_cat = Category.objects.create(name="Boiliai", slug="boiliai", parent=self.root_cat)
        self.spare = Category.objects.create(name="Atsarginės dalys", slug="atsargines-dalys")
        self.tents = Category.objects.create(name="Palapinės", slug="palapines")
        self.korda = Brand.objects.create(name="Korda", slug="korda")
        self.fox = Brand.objects.create(name="Fox", slug="fox")
        self.p_boilie = self._product("A1", "Mainline boilie", category=self.carp_cat)
        self.p_tent_korda = self._product("A2", "Korda palapinė", category=self.tents, brand=self.korda)
        self.p_tent = self._product("A3", "Turistinė palapinė", category=self.tents)
        self.p_spare = self._product("A4", "Korda spool", category=self.spare, brand=self.korda)
        self.p_fox = self._product("A5", "Fox catfish rig", brand=self.fox)

        self.fishing = Activity.objects.create(code="zvejyba", name="Žvejyba", min_products=1)
        self.carp = Activity.objects.create(code="karpine", name="Karpinė", parent=self.fishing, min_products=1)
        self.catfish = Activity.objects.create(code="samu", name="Šamų", parent=self.fishing, min_products=5)
        self.tourism = Activity.objects.create(code="turizmas", name="Turizmas", min_products=1)
        self.camp = Activity.objects.create(code="stovyklavimas", name="Stovyklavimas", parent=self.tourism, min_products=1)

    def _product(self, sku, name, **kw):
        p = Product.objects.create(sku=sku, name=name, slug=sku.lower(), tax_class=self.tax, is_active=True, **kw)
        Variant.objects.create(product=p, sku=f"{sku}-v", name=name, price_eur="10.00", is_active=True)
        return p

    def _rule(self, activity, name, cats=(), brands=(), excl=(), **kw):
        r = ActivityRule.objects.create(activity=activity, name=name, **kw)
        r.categories.set(cats)
        r.brands.set(brands)
        r.exclude_categories.set(excl)
        return r

    def _ids(self, activity):
        return set(
            ProductActivity.objects.filter(activity=activity, source="rule").values_list("product_id", flat=True)
        )

    def test_or_within_list_and_between_conditions(self):
        self._rule(self.carp, "cats", cats=[self.root_cat])
        self._rule(self.carp, "brands", brands=[self.korda, self.fox], cats=[self.tents])
        res = apply_activity_rules(reindex=False)
        self.assertEqual(res.added, 2)
        self.assertEqual(self._ids(self.carp), {self.p_boilie.id, self.p_tent_korda.id})

    def test_exclude_categories_and_empty_rule(self):
        self._rule(self.carp, "korda", brands=[self.korda], excl=[self.spare])
        self._rule(self.carp, "empty")
        apply_activity_rules(reindex=False)
        self.assertEqual(self._ids(self.carp), {self.p_tent_korda.id})

    def test_pattern_and_same_product_in_multiple_activities(self):
        self._rule(self.catfish, "text", pattern="catfish|šamų")
        self._rule(self.camp, "tents", cats=[self.tents])
        self._rule(self.carp, "korda", brands=[self.korda], cats=[self.tents])
        apply_activity_rules(reindex=False)
        self.assertEqual(self._ids(self.catfish), {self.p_fox.id})
        self.assertEqual(self._ids(self.camp), {self.p_tent.id, self.p_tent_korda.id})
        doc = product_document(Product.objects.get(id=self.p_tent_korda.id))
        self.assertEqual(doc["activities"], ["karpine", "stovyklavimas", "turizmas", "zvejyba"])

    def test_dry_run_and_removal(self):
        rule = self._rule(self.camp, "tents", cats=[self.tents])
        res = apply_activity_rules(dry_run=True, reindex=False)
        self.assertEqual((res.added, ProductActivity.objects.count()), (2, 0))
        apply_activity_rules(reindex=False)
        rule.is_active = False
        rule.save()
        res = apply_activity_rules(reindex=False)
        self.assertEqual((res.removed, ProductActivity.objects.count()), (2, 0))

    def test_manual_exclude_wins(self):
        self._rule(self.camp, "tents", cats=[self.tents])
        apply_activity_rules(reindex=False)
        ProductActivity.objects.create(product=self.p_tent, activity=self.camp, source="exclude")
        ProductActivity.objects.create(product=self.p_boilie, activity=self.camp, source="manual")
        counts = activity_product_counts()
        self.assertEqual(counts[self.camp.id], 2)
        self.assertEqual(counts[self.tourism.id], 2)
        self.assertEqual(product_document(Product.objects.get(id=self.p_tent.id))["activities"], [])

    def test_api_tree_detail_and_filter(self):
        self._rule(self.carp, "cats", cats=[self.root_cat])
        self._rule(self.carp, "korda", brands=[self.korda], cats=[self.tents])
        self._rule(self.camp, "tents", cats=[self.tents])
        self._rule(self.catfish, "text", pattern="catfish")
        apply_activity_rules(reindex=False)

        tree = self.client.get("/api/v1/catalog/activities").json()
        self.assertEqual([r["code"] for r in tree], ["turizmas", "zvejyba"] if tree[0]["code"] == "turizmas" else ["zvejyba", "turizmas"])
        fishing = next(r for r in tree if r["code"] == "zvejyba")
        self.assertEqual(fishing["product_count"], 3)
        self.assertEqual([c["code"] for c in fishing["children"]], ["karpine"])  # samu < min_products
        hidden = self.client.get("/api/v1/catalog/activities?include_hidden=true").json()
        fishing = next(r for r in hidden if r["code"] == "zvejyba")
        self.assertEqual({c["code"] for c in fishing["children"]}, {"karpine", "samu"})

        detail = self.client.get("/api/v1/catalog/activities/karpine").json()
        self.assertEqual(detail["parent"]["code"], "zvejyba")
        self.assertEqual(detail["product_count"], 2)
        self.assertEqual({c["slug"] for c in detail["top_categories"]}, {"boiliai", "palapines"})
        self.assertEqual(self.client.get("/api/v1/catalog/activities/nera").status_code, 404)

        page = self.client.get("/api/v1/catalog/products?activity=zvejyba").json()
        self.assertEqual(page["count"], 3)
        page = self.client.get("/api/v1/catalog/products?activity=stovyklavimas").json()
        self.assertEqual({i["slug"] for i in page["items"]}, {"a2", "a3"})
        self.assertEqual(self.client.get("/api/v1/catalog/products?activity=nera").status_code, 404)

    def test_reassigned_rule_moves_links(self):
        rule = self._rule(self.carp, "tents", cats=[self.tents])
        apply_activity_rules(reindex=False)
        rule.activity = self.camp
        rule.save()
        res = apply_activity_rules(reindex=False)
        self.assertEqual(res.removed, 2)
        self.assertEqual(self._ids(self.carp), set())
        self.assertEqual(self._ids(self.camp), {self.p_tent.id, self.p_tent_korda.id})

    def test_parent_exclude_and_inactive_child(self):
        from catalog.activities import activity_q

        self._rule(self.carp, "tents", cats=[self.tents])
        apply_activity_rules(reindex=False)
        ProductActivity.objects.create(product=self.p_tent, activity=self.fishing, source="exclude")
        self.assertEqual(product_document(Product.objects.get(id=self.p_tent.id))["activities"], [])
        self.assertEqual(activity_product_counts()[self.fishing.id], 1)
        self.assertEqual(
            set(Product.objects.filter(activity_q(self.carp)).values_list("id", flat=True)),
            {self.p_tent_korda.id},
        )
        self.carp.is_active = False
        self.carp.save()
        self.assertNotIn(self.fishing.id, activity_product_counts())


class SeedActivitiesUpdateTests(TestCase):
    def test_update_keeps_admin_exclusions_and_is_active(self):
        from django.core.management import call_command

        from catalog.models import ActivityRule

        Category.objects.create(name="Atsarginės dalys", slug="atsargines-dalys")
        custom = Category.objects.create(name="Kita", slug="kita")
        boilies = Category.objects.create(name="Boiliai", slug="boiliai")
        call_command("seed_activities", stdout=StringIO())
        rule = ActivityRule.objects.get(activity__code="karpine", name="Karpinės kategorijos")
        rule.exclude_categories.add(custom)
        rule.categories.clear()
        rule.is_active = True
        rule.save()

        call_command("seed_activities", stdout=StringIO())
        rule.refresh_from_db()
        self.assertEqual(list(rule.categories.all()), [])

        call_command("seed_activities", "--update", stdout=StringIO())
        rule.refresh_from_db()
        self.assertTrue(rule.is_active)
        self.assertIn(boilies, rule.categories.all())
        self.assertEqual(
            set(rule.exclude_categories.values_list("slug", flat=True)), {"atsargines-dalys", "kita"}
        )
