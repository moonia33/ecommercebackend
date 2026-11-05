import datetime

from django.test import TestCase
from django.utils import timezone

from catalog.badges import apply_badge_rules, badges_for_products, sync_import_badges
from catalog.models import (
    Badge,
    BadgeRule,
    BadgeTranslation,
    Brand,
    Feature,
    FeatureValue,
    InventoryItem,
    Product,
    ProductBadge,
    ProductFeatureValue,
    TaxClass,
    TaxRate,
    Variant,
    Warehouse,
)
from catalog.services.product_detail import build_product_detail_payload
from search.services import product_document


class BadgeTests(TestCase):
    def setUp(self):
        self.tax, _ = TaxClass.objects.get_or_create(code="standard", defaults={"name": "Standard"})
        TaxRate.objects.create(
            tax_class=self.tax, country_code="LT", rate="0.21",
            valid_from=datetime.date(2020, 1, 1), is_active=True,
        )
        self.brand = Brand.objects.create(name="Savage Gear", slug="savage-gear")
        self.wh = Warehouse.objects.create(code="albatros", name="Centrinis", country_code="LT")
        self.p1 = self._product("AL.1", "Savage Gear 3D Minnow", brand=self.brand)
        self.p2 = self._product("FX.2", "Fox rod")
        self.badge = Badge.objects.create(code="new-2027", name="Naujiena 2027", priority=5)

    def _product(self, sku, name, **kw):
        p = Product.objects.create(sku=sku, name=name, slug=sku.lower().replace(".", "-"),
                                   tax_class=self.tax, is_active=True, **kw)
        Variant.objects.create(product=p, sku=f"{sku}-v", name=name, price_eur="10.00", is_active=True)
        return p

    def test_rule_adds_and_removes(self):
        rule = BadgeRule.objects.create(name="AL", badge=self.badge, sku_prefix="AL.")
        res = apply_badge_rules(reindex=False)
        self.assertEqual((res.added, res.removed), (1, 0))
        self.assertTrue(ProductBadge.objects.filter(product=self.p1, rule=rule).exists())
        rule.sku_prefix = "FX."
        rule.save()
        res = apply_badge_rules(reindex=False)
        self.assertEqual((res.added, res.removed), (1, 1))
        self.assertEqual(
            list(ProductBadge.objects.values_list("product_id", flat=True)), [self.p2.id]
        )
        rule.is_active = False
        rule.save()
        apply_badge_rules(reindex=False)
        self.assertFalse(ProductBadge.objects.exists())

    def test_rule_keeps_manual(self):
        ProductBadge.objects.create(product=self.p2, badge=self.badge)
        BadgeRule.objects.create(name="AL", badge=self.badge, sku_prefix="AL.")
        apply_badge_rules(reindex=False)
        self.assertEqual(ProductBadge.objects.count(), 2)

    def test_dry_run_writes_nothing(self):
        BadgeRule.objects.create(name="AL", badge=self.badge, sku_prefix="AL.")
        res = apply_badge_rules(dry_run=True)
        self.assertEqual(res.added, 1)
        self.assertFalse(ProductBadge.objects.exists())

    def test_conditions_feature_text_stock_discount(self):
        feat = Feature.objects.create(code="sezonas", name="Sezonas")
        fv = FeatureValue.objects.create(feature=feat, value="2027")
        ProductFeatureValue.objects.create(product=self.p1, feature=feat, feature_value=fv)
        r_feat = BadgeRule.objects.create(name="f", badge=self.badge, feature=feat, feature_value=fv)
        r_text = BadgeRule.objects.create(name="t", badge=self.badge, pattern="3d minnow", brand=self.brand)
        r_stock = BadgeRule.objects.create(name="s", badge=self.badge, in_stock_only=True, warehouse=self.wh)
        r_disc = BadgeRule.objects.create(name="d", badge=self.badge, discounted_only=True)
        v2 = self.p2.variants.get()
        InventoryItem.objects.create(variant=v2, warehouse=self.wh, qty_on_hand=3, offer_discount_percent=30)
        res = apply_badge_rules(reindex=False)
        self.assertEqual(res.matched, {r_feat.id: 1, r_text.id: 1, r_stock.id: 1, r_disc.id: 1})
        self.assertTrue(ProductBadge.objects.filter(rule=r_stock, product=self.p2).exists())

    def test_import_sync(self):
        sync_import_badges(self.p1, ["new-2027", "centrinis"])
        self.assertTrue(Badge.objects.filter(code="centrinis").exists())
        self.assertEqual(self.p1.badge_links.count(), 2)
        sync_import_badges(self.p1, ["centrinis"])
        self.assertEqual(
            list(self.p1.badge_links.values_list("badge__code", flat=True)), ["centrinis"]
        )

    def test_output_live_only_and_translated(self):
        BadgeTranslation.objects.create(badge=self.badge, language_code="en", name="New 2027")
        expired = Badge.objects.create(
            code="old", name="Old", valid_to=timezone.localdate() - datetime.timedelta(days=1)
        )
        ProductBadge.objects.create(product=self.p1, badge=self.badge)
        ProductBadge.objects.create(product=self.p1, badge=self.badge, source="import")
        ProductBadge.objects.create(product=self.p1, badge=expired)
        out = badges_for_products([self.p1.id], language_code="en")[self.p1.id]
        self.assertEqual([b["code"] for b in out], ["new-2027"])
        self.assertEqual(out[0]["name"], "New 2027")
        payload = build_product_detail_payload(
            product=self.p1, shop_id=None, country_code="LT", channel="normal", language_code="lt"
        )
        self.assertEqual(payload["badges"][0]["name"], "Naujiena 2027")
        p = Product.objects.prefetch_related("badge_links__badge", "variants").get(id=self.p1.id)
        self.assertEqual(product_document(p)["badges"], ["new-2027"])
