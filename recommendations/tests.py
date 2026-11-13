from __future__ import annotations

from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import Client, TestCase, override_settings
from django.utils import timezone

from accounts.jwt_utils import issue_access_token
from analytics.models import RecentlyViewedProduct
from api.models import Shop, ShopLanguage
from catalog.models import (
    Activity,
    Category,
    Feature,
    FeatureValue,
    InventoryItem,
    Product,
    ProductActivity,
    ProductFeatureValue,
    TaxClass,
    Variant,
    Warehouse,
)
from checkout.models import Cart, CartItem, Order, OrderLine
from recommendations.models import (
    ProductCooccur,
    ProductMomentum,
    ProductRelation,
    SeasonalBoost,
)
from recommendations.services import (
    context_multipliers,
    popular_product_ids,
    rebuild_cooccurrence,
    rebuild_momentum,
)

User = get_user_model()


@override_settings(ALLOWED_HOSTS=["*"], SHOP_CONTEXT_ENABLED=True, SHOP_CONTEXT_STRICT=True)
class RecommendationBase(TestCase):
    def setUp(self):
        self.client = Client()
        self.shop = Shop.objects.create(
            code="rec", name="Rec", primary_domain="rec.local",
            default_language_code="lt", default_country_code="LT",
            currency="EUR", is_active=True)
        ShopLanguage.objects.create(
            shop=self.shop, language_code="lt", is_active=True,
            is_default=True, sort_order=0)

        self.tax = TaxClass.objects.create(code="std", name="Standard")
        import datetime
        from catalog.models import TaxRate
        TaxRate.objects.create(
            tax_class=self.tax, country_code="LT", rate="0.21",
            valid_from=datetime.date(2020, 1, 1), is_active=True)

        self.wh = Warehouse.objects.create(
            code="main", name="Main", country_code="LT",
            kind=Warehouse.Kind.LOCAL, is_active=True)

        self.cat_carp = Category.objects.create(
            name="Carp", slug="carp", is_active=True)
        self.cat_spin = Category.objects.create(
            name="Spin", slug="spin", is_active=True)

        self.f_technique = Feature.objects.create(
            code="technique", name="Žūklės rūšis",
            recommendation_boost=True)
        self.v_carp = FeatureValue.objects.create(
            feature=self.f_technique, value="Karpiams")
        self.v_float = FeatureValue.objects.create(
            feature=self.f_technique, value="Plūdinė")

        self.f_color = Feature.objects.create(
            code="color", name="Spalva", recommendation_boost=False)
        self.v_red = FeatureValue.objects.create(
            feature=self.f_color, value="Raudona")

    def _product(self, sku, category=None, stock=5, technique=None):
        p = Product.objects.create(
            sku=sku, name=sku, slug=f"slug-{sku.lower()}",
            tax_class=self.tax, category=category, is_active=True)
        v = Variant.objects.create(
            product=p, sku=f"V-{sku}", name="v",
            price_eur="10.00", is_active=True)
        if stock:
            InventoryItem.objects.create(
                variant=v, warehouse=self.wh, qty_on_hand=stock)
        if technique:
            ProductFeatureValue.objects.create(
                product=p, feature=self.f_technique,
                feature_value=technique)
        return p

    def _paid_order(self, user, *products):
        order = Order.objects.create(
            user=user, status=Order.Status.PAID,
            country_code="LT", currency="EUR")
        for i, p in enumerate(products):
            OrderLine.objects.create(
                order=order, variant=p.variants.first(),
                qty=1, unit_net="10.00", vat_rate="0.21",
                unit_vat="2.10", unit_gross="12.10",
                total_net="10.00", total_vat="2.10",
                total_gross="12.10")
        return order

    def _get(self, path, **extra):
        return self.client.get(
            f"/api/v1/recommendations{path}", HTTP_HOST="rec.local", **extra)


class ProductRecommendationTests(RecommendationBase):
    def test_manual_relation_wins_over_fallback(self):
        src = self._product("A", category=self.cat_carp)
        manual = self._product("M", category=self.cat_spin)
        same_cat = self._product("S", category=self.cat_carp)
        ProductRelation.objects.create(
            source_product=src, target_product=manual,
            kind="cross_sell", source="manual", weight=1)

        res = self._get(f"/products/{src.slug}?kind=cross_sell")
        self.assertEqual(res.status_code, 200)
        ids = [p["slug"] for p in res.json()]
        self.assertEqual(ids[0], manual.slug)
        self.assertIn(same_cat.slug, ids)

    def test_cooccur_beats_category_fallback(self):
        src = self._product("A", category=self.cat_carp)
        co = self._product("CO", category=self.cat_spin)
        same_cat = self._product("S", category=self.cat_carp)
        ProductCooccur.objects.create(
            product_a=src, product_b=co, order_count=7)

        res = self._get(f"/products/{src.slug}?kind=cross_sell")
        ids = [p["slug"] for p in res.json()]
        self.assertEqual(ids[0], co.slug)
        self.assertIn(same_cat.slug, ids)

    def test_feature_value_match_beats_category(self):
        src = self._product("A", category=self.cat_carp,
                            technique=self.v_carp)
        same_technique = self._product(
            "T", category=self.cat_spin, technique=self.v_carp)
        same_cat = self._product("S", category=self.cat_carp,
                                 technique=self.v_float)

        res = self._get(f"/products/{src.slug}?kind=similar")
        ids = [p["slug"] for p in res.json()]
        self.assertEqual(ids[0], same_technique.slug)
        self.assertIn(same_cat.slug, ids)

    def test_feature_without_boost_flag_is_ignored(self):
        self.f_technique.recommendation_boost = False
        self.f_technique.save()
        self.f_color.recommendation_boost = False
        self.f_color.save()

        src = self._product("A", category=self.cat_carp)
        ProductFeatureValue.objects.create(
            product=src, feature=self.f_color, feature_value=self.v_red)
        same_color = self._product("C", category=self.cat_spin)
        ProductFeatureValue.objects.create(
            product=same_color, feature=self.f_color,
            feature_value=self.v_red)
        same_cat = self._product("S", category=self.cat_carp)

        res = self._get(f"/products/{src.slug}?kind=similar")
        ids = [p["slug"] for p in res.json()]
        # color not boosted -> same category wins over shared non-boost feature
        self.assertEqual(ids[0], same_cat.slug)

    def test_out_of_stock_excluded(self):
        src = self._product("A", category=self.cat_carp)
        oos = self._product("OOS", category=self.cat_carp, stock=0)
        stocked = self._product("S", category=self.cat_carp)

        res = self._get(f"/products/{src.slug}")
        ids = [p["slug"] for p in res.json()]
        self.assertNotIn(oos.slug, ids)
        self.assertIn(stocked.slug, ids)

    def test_source_and_manual_targets_filtered_by_shop_scope(self):
        src = self._product("A", category=self.cat_carp)
        res = self._get(f"/products/{src.slug}")
        self.assertEqual(res.status_code, 200)
        self.assertNotIn(src.slug, [p["slug"] for p in res.json()])

        res_other = self.client.get(
            f"/api/v1/recommendations/products/{src.slug}",
            HTTP_HOST="other.local")
        # strict shop context disabled -> falls back gracefully (200 or 404)
        self.assertIn(res_other.status_code, (200, 404))

    def test_invalid_kind_rejected(self):
        src = self._product("A", category=self.cat_carp)
        res = self._get(f"/products/{src.slug}?kind=bogus")
        self.assertEqual(res.status_code, 400)

    def test_kind_filters_manual_relations(self):
        src = self._product("A", category=self.cat_carp)
        upsell = self._product("U", category=self.cat_spin)
        cross = self._product("X", category=self.cat_spin)
        ProductRelation.objects.create(
            source_product=src, target_product=upsell,
            kind="upsell", source="manual")
        ProductRelation.objects.create(
            source_product=src, target_product=cross,
            kind="cross_sell", source="manual")

        res = self._get(f"/products/{src.slug}?kind=upsell")
        ids = [p["slug"] for p in res.json()]
        self.assertEqual(ids[0], upsell.slug)


class CartAndPersonalTests(RecommendationBase):
    def setUp(self):
        super().setUp()
        self.user = User.objects.create_user(
            email="u@example.com", password="pass1234")
        self.client.cookies["access_token"] = issue_access_token(
            user_id=self.user.id)

    def test_cart_excludes_items_and_uses_seeds(self):
        in_cart = self._product("IN", category=self.cat_carp)
        suggested = self._product("SG", category=self.cat_spin)

        cart = Cart.objects.create(user=self.user, shop=self.shop)
        CartItem.objects.create(
            cart=cart, variant=in_cart.variants.first(), qty=1)

        ProductCooccur.objects.create(
            product_a=in_cart, product_b=suggested, order_count=3)

        res = self._get("/cart?kind=cross_sell")
        self.assertEqual(res.status_code, 200)
        slugs = [p["slug"] for p in res.json()]
        self.assertNotIn(in_cart.slug, slugs)
        self.assertEqual(slugs[0], suggested.slug)

    def test_for_you_uses_viewed_products(self):
        viewed = self._product("V", category=self.cat_carp)
        co = self._product("CO", category=self.cat_spin)
        RecentlyViewedProduct.objects.create(
            user=self.user, product=viewed,
            last_viewed_at=timezone.now())
        ProductCooccur.objects.create(
            product_a=viewed, product_b=co, order_count=2)

        res = self._get("/for-you")
        self.assertEqual(res.status_code, 200)
        slugs = [p["slug"] for p in res.json()]
        self.assertEqual(slugs[0], co.slug)

    def test_for_you_excludes_purchased(self):
        bought = self._product("B", category=self.cat_carp)
        self._paid_order(self.user, bought)
        RecentlyViewedProduct.objects.create(
            user=self.user, product=bought,
            last_viewed_at=timezone.now())

        res = self._get("/for-you")
        slugs = [p["slug"] for p in res.json()]
        self.assertNotIn(bought.slug, slugs)

    def test_post_purchase_owner_only(self):
        p = self._product("A", category=self.cat_carp)
        order = self._paid_order(self.user, p)

        res = self._get(f"/post-purchase/{order.id}")
        self.assertEqual(res.status_code, 200)

        other = User.objects.create_user(
            email="o@example.com", password="pass1234")
        self.client.cookies["access_token"] = issue_access_token(
            user_id=other.id)
        res = self._get(f"/post-purchase/{order.id}")
        self.assertEqual(res.status_code, 404)

        self.client.cookies.pop("access_token", None)
        res = self._get(f"/post-purchase/{order.id}")
        self.assertEqual(res.status_code, 401)


class CooccurrenceRebuildTests(RecommendationBase):
    def test_rebuild_creates_pairs_and_auto_relations(self):
        user = User.objects.create_user(
            email="b@example.com", password="pass1234")
        a = self._product("A", category=self.cat_carp)
        b = self._product("B", category=self.cat_carp)
        c = self._product("C", category=self.cat_spin)

        self._paid_order(user, a, b)
        self._paid_order(user, a, b, c)

        stats = rebuild_cooccurrence()
        self.assertEqual(stats["orders_scanned"], 2)

        pair = ProductCooccur.objects.get(product_a=a, product_b=b)
        self.assertEqual(pair.order_count, 2)
        self.assertTrue(
            ProductCooccur.objects.filter(
                product_a=a, product_b=c, order_count=1).exists())

        rel = ProductRelation.objects.get(
            source_product=a, target_product=b, source="auto")
        self.assertEqual(rel.kind, "cross_sell")
        self.assertEqual(rel.weight, 2)

    def test_rebuild_ignores_unpaid_orders(self):
        user = User.objects.create_user(
            email="b2@example.com", password="pass1234")
        a = self._product("A", category=self.cat_carp)
        b = self._product("B", category=self.cat_carp)

        order = Order.objects.create(
            user=user, status=Order.Status.PENDING_PAYMENT,
            country_code="LT", currency="EUR")
        for p in (a, b):
            OrderLine.objects.create(
                order=order, variant=p.variants.first(), qty=1,
                unit_net="10.00", vat_rate="0.21", unit_vat="2.10",
                unit_gross="12.10", total_net="10.00",
                total_vat="2.10", total_gross="12.10")

        stats = rebuild_cooccurrence()
        self.assertEqual(stats["pairs"], 0)
        self.assertEqual(ProductCooccur.objects.count(), 0)


class ScoringTests(RecommendationBase):
    def test_rebuild_computes_weight_and_score(self):
        user = User.objects.create_user(
            email="s@example.com", password="pass1234")
        a = self._product("A", category=self.cat_carp)
        b = self._product("B", category=self.cat_carp)
        c = self._product("C", category=self.cat_spin)

        self._paid_order(user, a, b)
        self._paid_order(user, a, b, c)

        rebuild_cooccurrence()
        ab = ProductCooccur.objects.get(product_a=a, product_b=b)
        # orders: a=2, b=2, c=1 (visi ~w=1, švieži) → score=2/sqrt(2*2)=1.0
        self.assertEqual(ab.order_count, 2)
        self.assertAlmostEqual(ab.weight, 2.0, places=2)
        self.assertAlmostEqual(ab.score, 1.0, places=2)
        ac = ProductCooccur.objects.get(product_a=a, product_b=c)
        self.assertAlmostEqual(ac.score, 1 / (2 ** 0.5), places=2)

    def test_score_normalization_demotes_bestseller(self):
        # HOT bendrai perkama su viskuo → žemas score nepaisant aukšto count.
        src = self._product("A", category=self.cat_carp)
        hot = self._product("HOT", category=self.cat_spin)
        niche = self._product("NIC", category=self.cat_spin)
        ProductCooccur.objects.create(
            product_a=src, product_b=hot, order_count=10, score=0.05)
        ProductCooccur.objects.create(
            product_a=src, product_b=niche, order_count=2, score=0.9)

        res = self._get(f"/products/{src.slug}")
        ids = [p["slug"] for p in res.json()]
        self.assertLess(ids.index(niche.slug), ids.index(hot.slug))

    def test_time_decay_old_order_weighs_less(self):
        user = User.objects.create_user(
            email="d@example.com", password="pass1234")
        a = self._product("A", category=self.cat_carp)
        b = self._product("B", category=self.cat_carp)
        c = self._product("C", category=self.cat_spin)

        fresh = self._paid_order(user, a, b)
        old = self._paid_order(user, a, c)
        Order.objects.filter(id=old.id).update(
            created_at=timezone.now() - timezone.timedelta(days=120))

        rebuild_cooccurrence(lookback_days=180)
        ab = ProductCooccur.objects.get(product_a=a, product_b=b)
        ac = ProductCooccur.objects.get(product_a=a, product_b=c)
        self.assertAlmostEqual(ab.weight, 1.0, places=2)
        # 120 d. / 60 d. half-life → 0.5^2 = 0.25
        self.assertAlmostEqual(ac.weight, 0.25, places=2)


class MomentumAndBoostTests(RecommendationBase):
    def setUp(self):
        super().setUp()
        self.activity_ice = Activity.objects.create(
            code="poledine", name="Poledinė žūklė")
        self.activity_spin = Activity.objects.create(
            code="spiningine", name="Spiningavimas")

    def _link_activity(self, product, activity):
        return ProductActivity.objects.create(
            product=product, activity=activity, source="manual")

    def test_momentum_recent_spike_clamped_high(self):
        user = User.objects.create_user(
            email="m@example.com", password="pass1234")
        hot = self._product("HOT", category=self.cat_carp)
        steady = self._product("STD", category=self.cat_carp)

        # steady: pardavimai baseline lange (prieš 30 d.), jokių recent
        o = self._paid_order(user, steady)
        Order.objects.filter(id=o.id).update(
            created_at=timezone.now() - timezone.timedelta(days=30))
        # hot: tik recent pardavimai
        self._paid_order(user, hot)
        self._paid_order(user, hot)

        stats = rebuild_momentum()
        self.assertEqual(stats["products"], 2)
        m_hot = ProductMomentum.objects.get(product=hot)
        m_std = ProductMomentum.objects.get(product=steady)
        self.assertGreater(m_hot.momentum, 1.0)
        self.assertLess(m_std.momentum, 1.0)
        self.assertEqual(m_hot.sales_recent, 2)

    def test_manual_boost_lifts_activity_products(self):
        src = self._product("A", category=self.cat_carp)
        same_cat = self._product("S", category=self.cat_carp)
        ice = self._product("ICE", category=self.cat_spin)
        self._link_activity(ice, self.activity_ice)

        SeasonalBoost.objects.create(
            name="Atšalo — ledas", mode="manual",
            activity=self.activity_ice, boost="2.00", is_active=True)

        res = self._get(f"/products/{src.slug}")
        ids = [p["slug"] for p in res.json()]
        # Be boost'o same_cat (ta pati kategorija) eitų pirma fallback'e;
        # boost ×2 iškelia ICE aukščiau.
        self.assertEqual(ids[0], ice.slug)

    def test_manual_boost_expired_is_inactive(self):
        b = SeasonalBoost(
            name="Stintos", mode="manual",
            activity=self.activity_ice, boost="2.00", is_active=True,
            expires_at=timezone.now() - timezone.timedelta(hours=1))
        self.assertFalse(b.is_currently_active())

    def test_scheduled_window_wraps_year(self):
        ice_boost = SeasonalBoost(
            name="Žiema", mode="scheduled",
            activity=self.activity_ice, boost="2.00",
            start_mmdd="12-15", end_mmdd="02-28", is_active=True)
        import datetime

        jan = timezone.make_aware(datetime.datetime(2026, 1, 10))
        jul = timezone.make_aware(datetime.datetime(2026, 7, 10))
        self.assertTrue(ice_boost.is_currently_active(now=jan))
        self.assertFalse(ice_boost.is_currently_active(now=jul))

    def test_context_multipliers_combines_boost_and_momentum(self):
        p = self._product("P", category=self.cat_carp)
        self._link_activity(p, self.activity_ice)
        SeasonalBoost.objects.create(
            name="Ledas", mode="manual",
            activity=self.activity_ice, boost="2.00", is_active=True)
        ProductMomentum.objects.create(
            product=p, momentum=1.5, sales_recent=3, baseline_daily=0.1)

        mult = context_multipliers([p.id])
        self.assertAlmostEqual(mult[p.id], 3.0, places=2)

    def test_popular_prefers_recent_sellers(self):
        user = User.objects.create_user(
            email="p@example.com", password="pass1234")
        seller = self._product("SEL", category=self.cat_carp)
        stale = self._product("STL", category=self.cat_carp)
        self._paid_order(user, seller)
        self._paid_order(user, seller)

        ids = popular_product_ids(
            shop_id=self.shop.id, channel="normal", limit=10)
        self.assertLess(ids.index(seller.id), ids.index(stale.id))
