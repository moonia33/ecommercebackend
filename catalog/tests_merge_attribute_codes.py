from io import StringIO

from django.core.management import call_command
from django.test import TestCase

from catalog.models import (
    Category,
    CategoryFacet,
    Feature,
    FeatureTranslation,
    FeatureValue,
    OptionType,
    OptionTypeTranslation,
    OptionValue,
    OptionValueGroup,
    Product,
    ProductFeatureValue,
    ProductOptionType,
    Variant,
    VariantOptionValue,
)


class MergeAttributeCodesTests(TestCase):
    def setUp(self):
        self.product = Product.objects.create(sku="P1", name="Kabliukai", slug="p1", is_active=True)
        self.v1 = Variant.objects.create(product=self.product, sku="P1-8", name="8", price_eur="1.00", is_active=True)
        self.v2 = Variant.objects.create(product=self.product, sku="P1-6", name="6", price_eur="1.00", is_active=True)
        self.legacy = OptionType.objects.create(code="kabliuku-dydis", name="Kabliukų dydis")
        OptionTypeTranslation.objects.create(option_type=self.legacy, language_code="lt", name="Kabliukų dydis")
        self.hook = OptionType.objects.create(code="hook-size", name="Hook size")
        ProductOptionType.objects.create(product=self.product, option_type=self.legacy)
        for variant, code in ((self.v1, "8"), (self.v2, "6")):
            value = OptionValue.objects.create(option_type=self.legacy, code=code, label=code)
            VariantOptionValue.objects.create(variant=variant, option_type=self.legacy, option_value=value)
        group = OptionValueGroup.objects.create(option_type=self.legacy, code="smulkus", label="Smulkūs")
        group.values.add(OptionValue.objects.get(option_type=self.legacy, code="8"))
        category = Category.objects.create(name="Kabliukai", slug="kabliukai", is_active=True)
        CategoryFacet.objects.create(category=category, code="kabliuku-dydis")

        self.spalva = Feature.objects.create(code="spalva", name="Spalva")
        FeatureTranslation.objects.create(feature=self.spalva, language_code="lt", name="Spalva")
        self.color = Feature.objects.create(code="color", name="Color")
        red_old = FeatureValue.objects.create(feature=self.spalva, value="raudona")
        red_new = FeatureValue.objects.create(feature=self.color, value="raudona")
        ProductFeatureValue.objects.create(product=self.product, feature=self.spalva, feature_value=red_old)
        ProductFeatureValue.objects.create(product=self.product, feature=self.color, feature_value=red_new)

    def _run(self, *args):
        out = StringIO()
        call_command("merge_attribute_codes", *args, stdout=out)
        return out.getvalue()

    def test_dry_run_changes_nothing(self):
        report = self._run("--code", "kabliuku-dydis", "--code", "spalva")
        self.assertIn("DRY-RUN", report)
        self.assertIn("variant_options_moved=2", report)
        self.assertTrue(OptionType.objects.filter(code="kabliuku-dydis").exists())
        self.assertFalse(CategoryFacet.objects.filter(code="hook-size").exists())
        self.assertEqual(VariantOptionValue.objects.filter(option_type=self.hook).count(), 0)

    def test_apply_moves_options_translations_groups_and_facets(self):
        self._run("--apply", "--code", "kabliuku-dydis")
        self.assertFalse(OptionType.objects.filter(code="kabliuku-dydis").exists())
        self.assertEqual(
            sorted(VariantOptionValue.objects.filter(option_type=self.hook).values_list("option_value__code", flat=True)),
            ["6", "8"],
        )
        self.assertTrue(ProductOptionType.objects.filter(product=self.product, option_type=self.hook).exists())
        self.assertEqual(self.hook.translations.get(language_code="lt").name, "Kabliukų dydis")
        group = OptionValueGroup.objects.get(code="smulkus")
        self.assertEqual(group.option_type, self.hook)
        self.assertEqual(list(group.values.values_list("code", flat=True)), ["8"])
        self.assertTrue(CategoryFacet.objects.filter(code="hook-size").exists())

    def test_apply_merges_duplicate_feature_values(self):
        self._run("--apply", "--code", "spalva")
        self.assertFalse(Feature.objects.filter(code="spalva").exists())
        self.assertEqual(ProductFeatureValue.objects.filter(product=self.product).count(), 1)
        self.assertEqual(self.color.translations.get(language_code="lt").name, "Spalva")

    def test_new_target_keeps_flags(self):
        talpa = Feature.objects.create(code="talpa", name="Talpa", is_active=False, recommendation_boost=True)
        FeatureValue.objects.create(feature=talpa, value="5l", is_active=False)
        self._run("--apply", "--code", "talpa")
        volume = Feature.objects.get(code="volume")
        self.assertFalse(volume.is_active)
        self.assertTrue(volume.recommendation_boost)
        self.assertFalse(volume.values.get(value="5l").is_active)

    def test_conflicting_variant_left_untouched(self):
        other = OptionValue.objects.create(option_type=self.hook, code="4", label="4")
        VariantOptionValue.objects.create(variant=self.v1, option_type=self.hook, option_value=other)
        report = self._run("--apply", "--code", "kabliuku-dydis")
        self.assertIn("Konfliktai (variantas jau turi tikslinę ašį su kita reikšme): 1", report)
        self.assertTrue(VariantOptionValue.objects.filter(variant=self.v1, option_type=self.legacy).exists())
        self.assertTrue(OptionType.objects.filter(code="kabliuku-dydis").exists())
        self.assertTrue(CategoryFacet.objects.filter(code="kabliuku-dydis").exists())
        self.assertTrue(CategoryFacet.objects.filter(code="hook-size").exists())


class LegacyAliasMapTests(TestCase):
    def test_targets_are_canonical_kebab_codes_not_legacy(self):
        import re

        from catalog.attribute_aliases import LEGACY_ATTRIBUTE_CODES

        for old, new in LEGACY_ATTRIBUTE_CODES.items():
            self.assertRegex(new, r"^[a-z0-9]+(-[a-z0-9]+)*$", old)
            self.assertNotIn(new, LEGACY_ATTRIBUTE_CODES, f"{old} -> {new} yra grandinė")
            self.assertNotEqual(old, new)
        self.assertTrue(all(re.fullmatch(r"[a-z0-9-]+", k) for k in LEGACY_ATTRIBUTE_CODES))
