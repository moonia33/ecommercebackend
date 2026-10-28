import datetime
from decimal import Decimal

from django.test import TestCase, override_settings

from catalog.localization import (
    localize_brand,
    localize_category,
    localize_feature,
    localize_feature_value,
    localize_option_type,
    localize_option_value,
    localize_product,
    resolve_brand_by_slug,
    resolve_category_by_slug,
    resolve_product_by_slug,
)
from catalog.models import (
    Brand,
    BrandTranslation,
    Category,
    CategoryTranslation,
    Feature,
    FeatureTranslation,
    FeatureValue,
    FeatureValueGroup,
    FeatureValueTranslation,
    OptionType,
    OptionTypeTranslation,
    OptionValue,
    OptionValueTranslation,
    Product,
    ProductFeatureValue,
    ProductTranslation,
    ProductVideo,
    TaxClass,
    TaxRate,
    Variant,
)
from catalog.services.product_detail import build_product_detail_payload
from catalog.services.product_facets import build_facets_payload
from catalog.services.query_filters import apply_catalog_filters
from catalog.services.value_groups import (
    auto_assign_feature_group,
    parse_numeric_range,
)


class CatalogLocalizationTests(TestCase):
    def setUp(self):
        self.tax_class, _ = TaxClass.objects.get_or_create(
            code="standard",
            defaults={"name": "Standard"},
        )

        self.category = Category.objects.create(name="Kompiuteriai", slug="kompiuteriai", is_active=True)
        CategoryTranslation.objects.create(
            category=self.category,
            language_code="pl",
            name="Datori",
            slug="datori",
            description="Apraksts LV",
            seo_title="SEO LV",
            seo_description="SEO apraksts LV",
            seo_keywords="datori,tehnika",
        )

        self.brand = Brand.objects.create(name="Gamintojas", slug="gamintojas", is_active=True)
        BrandTranslation.objects.create(
            brand=self.brand,
            language_code="pl",
            name="Razotajs",
            slug="razotajs",
        )

        self.product = Product.objects.create(
            sku="SKU-1",
            name="Nešiojamas kompiuteris",
            slug="nesiojamas-kompiuteris",
            description="LT aprašymas",
            seo_title="LT SEO",
            seo_description="LT SEO desc",
            seo_keywords="lt,katalogas",
            tax_class=self.tax_class,
            brand=self.brand,
            category=self.category,
            is_active=True,
        )
        ProductTranslation.objects.create(
            product=self.product,
            language_code="pl",
            name="Portativais dators",
            slug="portativais-dators",
            description="LV apraksts",
            seo_title="LV SEO",
            seo_description="LV SEO desc",
            seo_keywords="lv,katalogs",
        )

        self.feature = Feature.objects.create(code="screen-size", name="Screen size", is_active=True, is_filterable=True)
        self.feature_value = FeatureValue.objects.create(feature=self.feature, value='15.6"', is_active=True)
        FeatureTranslation.objects.create(feature=self.feature, language_code="pl", name="Ekrāna izmērs")
        FeatureValueTranslation.objects.create(feature_value=self.feature_value, language_code="pl", value='15,6"')

        self.option_type = OptionType.objects.create(code="color", name="Color", is_active=True)
        self.option_value = OptionValue.objects.create(option_type=self.option_type, code="black", label="Black", is_active=True)
        OptionTypeTranslation.objects.create(option_type=self.option_type, language_code="pl", name="Krāsa")
        OptionValueTranslation.objects.create(option_value=self.option_value, language_code="pl", label="Melna")

    def test_localize_objects_by_language(self):
        category_loc = localize_category(self.category, language_code="pl")
        brand_loc = localize_brand(self.brand, language_code="pl")
        product_loc = localize_product(self.product, language_code="pl")

        self.assertEqual(category_loc["name"], "Datori")
        self.assertEqual(category_loc["slug"], "datori")
        self.assertEqual(brand_loc["name"], "Razotajs")
        self.assertEqual(brand_loc["slug"], "razotajs")
        self.assertEqual(product_loc["name"], "Portativais dators")
        self.assertEqual(product_loc["slug"], "portativais-dators")

    def test_resolve_by_translated_slug(self):
        self.assertEqual(resolve_category_by_slug(slug="datori", language_code="pl", is_active=True).id, self.category.id)
        self.assertEqual(resolve_brand_by_slug(slug="razotajs", language_code="pl", is_active=True).id, self.brand.id)
        self.assertEqual(resolve_product_by_slug(slug="portativais-dators", language_code="pl", is_active=True).id, self.product.id)

    def test_catalog_filters_accept_translated_category_and_brand_slug(self):
        qs = Product.objects.filter(is_active=True)
        filtered, selected_category = apply_catalog_filters(
            qs=qs,
            category_slug="datori",
            brand_slug="razotajs",
            language_code="pl",
        )

        self.assertIsNotNone(selected_category)
        self.assertEqual(selected_category.id, self.category.id)
        self.assertEqual(list(filtered.values_list("id", flat=True)), [self.product.id])

    def test_feature_and_option_localization(self):
        self.assertEqual(localize_feature(self.feature, language_code="pl"), "Ekrāna izmērs")
        self.assertEqual(localize_feature_value(self.feature_value, language_code="pl"), '15,6"')
        self.assertEqual(localize_option_type(self.option_type, language_code="pl"), "Krāsa")
        self.assertEqual(localize_option_value(self.option_value, language_code="pl"), "Melna")


class ProductDetailVideoTests(TestCase):
    def setUp(self):
        tax_class, _ = TaxClass.objects.get_or_create(code="standard", defaults={"name": "Standard"})
        TaxRate.objects.create(
            tax_class=tax_class,
            country_code="LT",
            rate="0.21",
            valid_from=datetime.date(2020, 1, 1),
            is_active=True,
        )
        self.product = Product.objects.create(
            sku="VID-1",
            name="Video Product",
            slug="video-product",
            tax_class=tax_class,
            is_active=True,
        )
        Variant.objects.create(product=self.product, sku="VID-1-A", name="A", price_eur="10.00", is_active=True)

        ProductVideo.objects.create(product=self.product, url="https://youtu.be/generic", sort_order=1)
        ProductVideo.objects.create(product=self.product, url="https://youtu.be/lt", language_code="lt", sort_order=2)
        ProductVideo.objects.create(product=self.product, url="https://youtu.be/de", language_code="de", sort_order=3)

    def test_detail_payload_returns_videos_filtered_by_language(self):
        payload = build_product_detail_payload(
            product=self.product,
            shop_id=None,
            country_code="LT",
            channel="normal",
            language_code="lt",
        )
        urls = [v["url"] for v in payload["videos"]]
        self.assertEqual(urls, ["https://youtu.be/generic", "https://youtu.be/lt"])


class FeatureValueGroupTests(TestCase):
    def setUp(self):
        self.feature = Feature.objects.create(
            code="casting_weight", name="Casting Weight",
            is_active=True, is_filterable=True,
        )
        self.v_light = FeatureValue.objects.create(feature=self.feature, value="10-30")
        self.v_light2 = FeatureValue.objects.create(feature=self.feature, value="5-15")
        self.v_heavy = FeatureValue.objects.create(feature=self.feature, value="20-100")
        self.v_weird = FeatureValue.objects.create(feature=self.feature, value="green")

        self.p1 = Product.objects.create(sku="P1", name="Light rod", slug="p1", is_active=True)
        self.p2 = Product.objects.create(sku="P2", name="Heavy rod", slug="p2", is_active=True)
        ProductFeatureValue.objects.create(product=self.p1, feature=self.feature, feature_value=self.v_light)
        ProductFeatureValue.objects.create(product=self.p2, feature=self.feature, feature_value=self.v_heavy)
        ProductFeatureValue.objects.create(product=self.p2, feature=self.feature, feature_value=self.v_weird)

        self.group = FeatureValueGroup.objects.create(
            feature=self.feature, code="iki-35", label="iki 35 g", max_value=35,
        )

    def test_parse_numeric_range(self):
        from decimal import Decimal

        self.assertEqual(parse_numeric_range("10-30"), (Decimal("10"), Decimal("30")))
        self.assertEqual(parse_numeric_range("30 g"), (Decimal("30"), Decimal("30")))
        self.assertEqual(parse_numeric_range("10,5-30"), (Decimal("10.5"), Decimal("30")))
        self.assertIsNone(parse_numeric_range("12cm/4.75in"))
        self.assertIsNone(parse_numeric_range("green"))

    def test_auto_assign_fits_within_bounds(self):
        added = auto_assign_feature_group(self.group)
        self.assertEqual(added, 2)
        members = set(self.group.values.values_list("value", flat=True))
        self.assertEqual(members, {"10-30", "5-15"})

    def test_group_code_filters_products(self):
        auto_assign_feature_group(self.group)
        qs, _ = apply_catalog_filters(
            qs=Product.objects.filter(is_active=True),
            feature="casting_weight:iki-35",
        )
        self.assertEqual(list(qs.values_list("id", flat=True)), [self.p1.id])

    def test_raw_value_filter_still_works(self):
        qs, _ = apply_catalog_filters(
            qs=Product.objects.filter(is_active=True),
            feature="casting_weight:20-100",
        )
        self.assertEqual(list(qs.values_list("id", flat=True)), [self.p2.id])

    def test_facets_emit_group_instead_of_member_values(self):
        auto_assign_feature_group(self.group)
        payload = build_facets_payload(
            product_ids=Product.objects.filter(is_active=True).values_list("id", flat=True),
            selected_category=None,
            language_code="lt",
        )
        feat = next(f for f in payload["features"] if f["code"] == "casting_weight")
        values = [(v["value"], v["label"]) for v in feat["values"]]
        self.assertIn(("iki-35", "iki 35 g"), values)
        self.assertIn(("20-100", "20-100"), values)  # ungrouped stays raw
        self.assertIn(("green", "green"), values)
        self.assertNotIn(("10-30", "10-30"), values)  # grouped value hidden


class NumericValueGroupTests(TestCase):
    """auto_assign_* naudoja value_num/value_num_max kai jie užpildyti."""

    def setUp(self):
        from decimal import Decimal

        self.feature = Feature.objects.create(
            code="line_strength", name="Line Strength",
            is_active=True, is_filterable=True,
        )
        # Tekstas neparsinamas ("green") — bet value_num turi tilpti.
        self.v_ok = FeatureValue.objects.create(
            feature=self.feature, value="green", value_num=Decimal("20"),
        )
        self.v_far = FeatureValue.objects.create(
            feature=self.feature, value="blue", value_num=Decimal("300"),
        )
        self.v_text = FeatureValue.objects.create(
            feature=self.feature, value="10-30",
        )
        self.group = FeatureValueGroup.objects.create(
            feature=self.feature, code="iki-50", label="iki 50 kg",
            max_value=Decimal("50"),
        )

    def test_value_num_used_without_text_parsing(self):
        added = auto_assign_feature_group(self.group)
        self.assertEqual(added, 2)
        members = set(self.group.values.values_list("value", flat=True))
        self.assertEqual(members, {"green", "10-30"})

    def test_range_uses_num_max(self):
        from decimal import Decimal

        v = FeatureValue.objects.create(
            feature=self.feature, value="r", value_num=Decimal("10"),
            value_num_max=Decimal("80"),
        )
        added = auto_assign_feature_group(self.group)
        self.assertFalse(self.group.values.filter(pk=v.pk).exists())


class ProductFeatureNumericOutTests(TestCase):
    def test_value_num_serialized(self):
        from decimal import Decimal

        tc, _ = TaxClass.objects.get_or_create(
            code="standard", defaults={"name": "Standard"}
        )
        TaxRate.objects.create(
            tax_class=tc, country_code="LT", rate="0.21",
            valid_from=datetime.date(2020, 1, 1), is_active=True,
        )
        product = Product.objects.create(
            sku="NUM-1", name="Num", slug="num-1", is_active=True,
            tax_class=tc,
        )
        Variant.objects.create(
            product=product, sku="NUM-1-A", price_eur="10.00", is_active=True,
        )
        feature = Feature.objects.create(code="weight", name="Weight")
        value = FeatureValue.objects.create(
            feature=feature, value="85-g", value_num=Decimal("85"), unit="g",
        )
        ProductFeatureValue.objects.create(
            product=product, feature=feature, feature_value=value,
        )
        payload = build_product_detail_payload(
            product=product, shop_id=None, country_code="LT",
            channel="normal", language_code="lt",
        )
        f = next(f for f in payload["features"] if f["feature_code"] == "weight")
        self.assertEqual(f["value_num"], Decimal("85"))
        self.assertEqual(f["unit"], "g")


class EngineFacetsTests(TestCase):
    """Typesense facet_by counts kelias: used-set'ai iš indekso vietoj
    ORM distinct-scan'ų; ne-filterable ir stale kodai atmesti."""

    def setUp(self):
        self.brand = Brand.objects.create(name="B", slug="b", is_active=True)
        self.cat = Category.objects.create(name="C", slug="c", is_active=True)
        self.product = Product.objects.create(
            sku="E-1", name="E", slug="e-1", is_active=True,
            brand=self.brand, category=self.cat,
        )
        self.feature = Feature.objects.create(
            code="weight", name="Weight", is_active=True, is_filterable=True,
        )
        self.fv = FeatureValue.objects.create(
            feature=self.feature, value="85-g")
        ProductFeatureValue.objects.create(
            product=self.product, feature=self.feature, feature_value=self.fv,
        )
        # Spec-only feature — neturi atsirasti facetuose net jei indeksas
        # jį turi (stale dokumentai).
        self.spec_feature = Feature.objects.create(
            code="material-fabric", name="Material", is_active=True,
            is_filterable=False,
        )
        self.spec_fv = FeatureValue.objects.create(
            feature=self.spec_feature, value="cotton")
        ProductFeatureValue.objects.create(
            product=self.product, feature=self.spec_feature,
            feature_value=self.spec_fv,
        )

    def _engine_counts(self):
        return {
            "brand_id": {str(self.brand.id): 1},
            "category_ids": {str(self.cat.id): 1},
            "group_id": {},
            "feature_facets": {
                "weight:85-g": 1,
                "material-fabric:cotton": 1,  # spec-only — atmestas
                "ghost-code:zz": 1,            # indekse likęs stale kodas
            },
        }

    def test_engine_counts_match_orm_payload(self):
        ids = Product.objects.filter(is_active=True).values_list("id", flat=True)
        orm = build_facets_payload(
            product_ids=ids, selected_category=None, language_code="lt")
        eng = build_facets_payload(
            product_ids=ids, selected_category=None, language_code="lt",
            engine_counts=self._engine_counts())
        self.assertEqual(
            [f["code"] for f in eng["features"]],
            [f["code"] for f in orm["features"]],
        )
        self.assertEqual(
            {v["value"] for f in eng["features"] for v in f["values"]},
            {v["value"] for f in orm["features"] for v in f["values"]},
        )
        self.assertEqual(
            [b["id"] for b in eng["brands"]],
            [b["id"] for b in orm["brands"]],
        )

    def test_spec_only_and_stale_codes_dropped(self):
        ids = Product.objects.filter(is_active=True).values_list("id", flat=True)
        payload = build_facets_payload(
            product_ids=ids, selected_category=None, language_code="lt",
            engine_counts=self._engine_counts())
        codes = {f["code"] for f in payload["features"]}
        self.assertEqual(codes, {"weight"})


class FacetCodeMergeTests(TestCase):
    """Feature ir OptionType su tuo pačiu code -> vienas facetas."""

    def test_same_code_merges_into_single_facet(self):
        product = Product.objects.create(
            sku="M-1", name="M", slug="m-1", is_active=True,
        )
        variant = Variant.objects.create(
            product=product, sku="M-1-A", price_eur="10.00", is_active=True,
        )
        feature = Feature.objects.create(code="weight", name="Weight")
        fv = FeatureValue.objects.create(feature=feature, value="85-g")
        ProductFeatureValue.objects.create(
            product=product, feature=feature, feature_value=fv,
        )
        opt_type = OptionType.objects.create(code="weight", name="Weight opt")
        ov = OptionValue.objects.create(
            option_type=opt_type, code="90-g", label="90 g",
        )
        from catalog.models import VariantOptionValue
        VariantOptionValue.objects.create(
            variant=variant, option_type=opt_type, option_value=ov,
        )
        payload = build_facets_payload(
            product_ids=[product.id],
            selected_category=None,
            language_code="lt",
        )
        weights_f = [f for f in payload["features"] if f["code"] == "weight"]
        weights_o = [t for t in payload["option_types"] if t["code"] == "weight"]
        self.assertEqual(len(weights_f), 1)
        self.assertEqual(weights_o, [])
        vals = {v["value"] for v in weights_f[0]["values"]}
        self.assertEqual(vals, {"85-g", "90-g"})

        # Ir filtras per feature= randa abi reikšmes (ir option pusę).
        qs, _ = apply_catalog_filters(
            qs=Product.objects.filter(is_active=True), feature="weight:90-g",
        )
        self.assertEqual(list(qs.values_list("id", flat=True)), [product.id])


class VariantNameOutTests(TestCase):
    def test_variant_name_serialized(self):
        tc, _ = TaxClass.objects.get_or_create(
            code="standard", defaults={"name": "Standard"}
        )
        TaxRate.objects.create(
            tax_class=tc, country_code="LT", rate="0.21",
            valid_from=datetime.date(2020, 1, 1), is_active=True,
        )
        product = Product.objects.create(
            sku="VN-1", name="VN", slug="vn-1", is_active=True, tax_class=tc,
        )
        Variant.objects.create(
            product=product, sku="VN-1-A", name="2.70m / 10-40g",
            price_eur="10.00", is_active=True,
        )
        payload = build_product_detail_payload(
            product=product, shop_id=None, country_code="LT",
            channel="normal", language_code="lt",
        )
        self.assertEqual(payload["variants"][0]["name"], "2.70m / 10-40g")


class PruneOrphanAttributesTests(TestCase):
    """prune_orphan_attributes: --dry-run default, --apply trina."""

    def _run_cmd(self, *args):
        from io import StringIO

        from django.core.management import call_command

        out = StringIO()
        call_command("prune_orphan_attributes", *args, stdout=out)
        return out.getvalue()

    def _feature_value(self, code="weight", value="10-kg"):
        from catalog.models import FeatureValue

        feature = Feature.objects.create(code=code, name=code)
        return feature, FeatureValue.objects.create(feature=feature, value=value)

    def test_unreferenced_feature_value_deleted_feature_cleaned(self):
        feature, fv = self._feature_value()
        out = self._run_cmd("--apply")
        self.assertFalse(FeatureValue.objects.filter(pk=fv.pk).exists())
        # Feature'ui neliko values/assignments -> ir jis ištrintas.
        self.assertFalse(Feature.objects.filter(pk=feature.pk).exists())
        self.assertIn("FeatureValue: deleted 1", out)
        self.assertIn("Feature: deleted 1", out)

    def test_referenced_feature_value_and_feature_kept(self):
        feature, fv = self._feature_value()
        ProductFeatureValue.objects.create(
            product=Product.objects.create(sku="k1", name="K", slug="k1"),
            feature=feature, feature_value=fv,
        )
        self._run_cmd("--apply")
        self.assertTrue(FeatureValue.objects.filter(pk=fv.pk).exists())
        self.assertTrue(Feature.objects.filter(pk=feature.pk).exists())

    def test_variant_reference_counts(self):
        from catalog.models import VariantFeatureValue

        feature, fv = self._feature_value()
        product = Product.objects.create(sku="k2", name="K2", slug="k2")
        variant = Variant.objects.create(
            product=product, sku="k2-v", price_eur="1.00"
        )
        VariantFeatureValue.objects.create(
            variant=variant, feature=feature, feature_value=fv,
        )
        self._run_cmd("--apply")
        self.assertTrue(FeatureValue.objects.filter(pk=fv.pk).exists())

    def test_unreferenced_option_value_deleted_type_cleaned(self):
        ot = OptionType.objects.create(code="size", name="Size")
        ov = OptionValue.objects.create(
            option_type=ot, code="xxl", label="XXL"
        )
        self._run_cmd("--apply")
        self.assertFalse(OptionValue.objects.filter(pk=ov.pk).exists())
        self.assertFalse(OptionType.objects.filter(pk=ot.pk).exists())

    def test_referenced_option_value_kept(self):
        from catalog.models import VariantOptionValue

        ot = OptionType.objects.create(code="color", name="Color")
        ov = OptionValue.objects.create(
            option_type=ot, code="red", label="Red"
        )
        product = Product.objects.create(sku="k3", name="K3", slug="k3")
        variant = Variant.objects.create(
            product=product, sku="k3-v", price_eur="1.00"
        )
        VariantOptionValue.objects.create(
            variant=variant, option_type=ot, option_value=ov
        )
        self._run_cmd("--apply")
        self.assertTrue(OptionValue.objects.filter(pk=ov.pk).exists())
        self.assertTrue(OptionType.objects.filter(pk=ot.pk).exists())

    def test_product_option_type_reference_keeps_type(self):
        from catalog.models import ProductOptionType

        ot = OptionType.objects.create(code="len", name="Len")
        product = Product.objects.create(sku="k4", name="K4", slug="k4")
        ProductOptionType.objects.create(product=product, option_type=ot)
        self._run_cmd("--apply")
        self.assertTrue(OptionType.objects.filter(pk=ot.pk).exists())

    def test_underscore_code_with_dash_twin_deleted(self):
        # 'casting_weight' tuščias feature'as su '-' dvyniu -> ištrinamas.
        f_under = Feature.objects.create(code="casting_weight", name="CW old")
        f_twin = Feature.objects.create(code="casting-weight", name="CW")
        fv_twin = FeatureValue.objects.create(feature=f_twin, value="10-g")
        # Dvynis turi referenciją -> lieka.
        ProductFeatureValue.objects.create(
            product=Product.objects.create(sku="k7", name="K7", slug="k7"),
            feature=f_twin, feature_value=fv_twin,
        )
        self._run_cmd("--apply")
        self.assertFalse(Feature.objects.filter(pk=f_under.pk).exists())
        self.assertTrue(Feature.objects.filter(pk=f_twin.pk).exists())

    def test_underscore_twin_feature_with_references_skipped(self):
        # Referencuotas '_' feature'as su dvyniu -> kandidatas, bet PROTECT.
        f_under = Feature.objects.create(code="line_strength", name="LS old")
        fv = FeatureValue.objects.create(feature=f_under, value="20-kg")
        Feature.objects.create(code="line-strength", name="LS")
        ProductFeatureValue.objects.create(
            product=Product.objects.create(sku="k8", name="K8", slug="k8"),
            feature=f_under, feature_value=fv,
        )
        out = self._run_cmd("--apply")
        self.assertTrue(Feature.objects.filter(pk=f_under.pk).exists())
        self.assertIn("skipped", out)

    def test_underscore_code_without_twin_and_referenced_kept(self):
        ot = OptionType.objects.create(code="rod_length", name="Len")
        ov = OptionValue.objects.create(
            option_type=ot, code="2_7m", label="2.7m"
        )
        product = Product.objects.create(sku="k5", name="K5", slug="k5")
        variant = Variant.objects.create(
            product=product, sku="k5-v", price_eur="1.00"
        )
        from catalog.models import VariantOptionValue

        VariantOptionValue.objects.create(
            variant=variant, option_type=ot, option_value=ov
        )
        self._run_cmd("--apply")
        # Nėra '-' dvynio ir yra referencijos -> nieko netrina.
        self.assertTrue(OptionValue.objects.filter(pk=ov.pk).exists())
        self.assertTrue(OptionType.objects.filter(pk=ot.pk).exists())

    def test_referenced_underscore_twin_skipped_not_deleted(self):
        # Referencuojamas '_' option value su '-' dvyniu: kandidatas,
        # bet PROTECT -> 'skipped', DB lieka.
        from catalog.models import VariantOptionValue

        ot = OptionType.objects.create(code="size2", name="S2")
        ov = OptionValue.objects.create(
            option_type=ot, code="x_l", label="XL"
        )
        OptionValue.objects.create(option_type=ot, code="x-l", label="XL")
        product = Product.objects.create(sku="k6", name="K6", slug="k6")
        variant = Variant.objects.create(
            product=product, sku="k6-v", price_eur="1.00"
        )
        VariantOptionValue.objects.create(
            variant=variant, option_type=ot, option_value=ov
        )
        out = self._run_cmd("--apply")
        self.assertTrue(OptionValue.objects.filter(pk=ov.pk).exists())
        self.assertIn("skipped 1", out)

    def test_dry_run_is_default_and_writes_nothing(self):
        feature, fv = self._feature_value()
        out = self._run_cmd()
        self.assertIn("dry-run", out)
        self.assertIn("would delete", out)
        self.assertTrue(FeatureValue.objects.filter(pk=fv.pk).exists())
        self.assertTrue(Feature.objects.filter(pk=feature.pk).exists())


class CategoryFacetPayloadTests(TestCase):
    """F4: per-category facet configuration + numeric range facets."""

    def setUp(self):
        from decimal import Decimal

        from catalog.models import CategoryFacet, VariantOptionValue

        self.cat = Category.objects.create(
            name="Valai", slug="valai", is_active=True
        )
        self.other_cat = Category.objects.create(
            name="Kiti", slug="kiti", is_active=True
        )
        self.product = Product.objects.create(
            sku="RF-1", name="Valas", slug="rf-1",
            category=self.cat, is_active=True,
        )
        Variant.objects.create(
            product=self.product, sku="RF-1-A", price_eur="1.00",
            is_active=True,
        )

        self.diameter = Feature.objects.create(
            code="line-diameter", name="Line diameter",
            datatype="number", unit="mm",
            is_active=True, is_filterable=True,
        )
        self.d1 = FeatureValue.objects.create(
            feature=self.diameter, value="0.20", value_num=Decimal("0.20"),
        )
        self.d2 = FeatureValue.objects.create(
            feature=self.diameter, value="0.26", value_num=Decimal("0.26"),
        )
        self.color = Feature.objects.create(
            code="color", name="Color", is_active=True, is_filterable=True,
        )
        self.c1 = FeatureValue.objects.create(
            feature=self.color, value="green",
        )
        self.hidden = Feature.objects.create(
            code="weight", name="Weight", is_active=True, is_filterable=True,
        )
        self.w1 = FeatureValue.objects.create(
            feature=self.hidden, value="5-g", value_num=Decimal("5"),
        )
        for fv in (self.d1, self.d2, self.c1, self.w1):
            ProductFeatureValue.objects.create(
                product=self.product,
                feature=fv.feature,
                feature_value=fv,
            )

        # Option-only facet (size) — used via variant option values.
        self.size_type = OptionType.objects.create(
            code="size", name="Size", is_active=True,
        )
        self.s1 = OptionValue.objects.create(
            option_type=self.size_type, code="m", label="M",
        )
        VariantOptionValue.objects.create(
            variant=self.product.variants.first(),
            option_type=self.size_type,
            option_value=self.s1,
        )

        self._CategoryFacet = CategoryFacet

    def _payload(self):
        return build_facets_payload(
            product_ids=[self.product.id],
            selected_category=self.cat,
            language_code="lt",
        )

    def test_no_config_returns_all_filterable(self):
        payload = self._payload()
        codes = {f["code"] for f in payload["features"]}
        self.assertIn("line-diameter", codes)
        self.assertIn("color", codes)
        self.assertIn("weight", codes)
        for f in payload["features"]:
            self.assertEqual(f["widget"], "checkbox")
            self.assertTrue(f["values"])

    def test_config_restricts_and_orders_facets(self):
        CF = self._CategoryFacet
        CF.objects.create(
            category=self.cat, code="color", widget="checkbox", order=20,
        )
        CF.objects.create(
            category=self.cat, code="line-diameter", widget="range", order=10,
        )
        CF.objects.create(
            category=self.cat, code="weight", order=30, is_enabled=False,
        )
        payload = self._payload()
        feats = payload["features"]
        # Only enabled, configured codes — in `order`, not model ordering.
        self.assertEqual([f["code"] for f in feats],
                         ["line-diameter", "color"])
        self.assertNotIn("weight", {f["code"] for f in feats})
        self.assertEqual(payload["option_types"], [])

    def test_range_facet_carries_min_max_unit_no_values(self):
        self._CategoryFacet.objects.create(
            category=self.cat, code="line-diameter", widget="range", order=10,
        )
        payload = self._payload()
        feat = next(
            f for f in payload["features"] if f["code"] == "line-diameter"
        )
        self.assertEqual(feat["widget"], "range")
        self.assertEqual(feat["values"], [])
        self.assertEqual(feat["min"], Decimal("0.20"))
        self.assertEqual(feat["max"], Decimal("0.26"))
        self.assertEqual(feat["unit"], "mm")

    def test_option_code_facet_configured_by_source(self):
        CF = self._CategoryFacet
        CF.objects.create(
            category=self.cat, code="size", source="option",
            widget="checkbox", order=10,
        )
        payload = self._payload()
        self.assertEqual(payload["features"], [])
        types = payload["option_types"]
        self.assertEqual([t["code"] for t in types], ["size"])
        self.assertEqual(types[0]["widget"], "checkbox")

    def test_config_on_other_category_does_not_apply(self):
        self._CategoryFacet.objects.create(
            category=self.other_cat, code="color", order=10,
        )
        payload = self._payload()
        codes = {f["code"] for f in payload["features"]}
        self.assertIn("line-diameter", codes)
        self.assertIn("weight", codes)

    def test_parent_config_inherited_by_child(self):
        """Tėvo CategoryFacet galioja vaiko kategorijai."""
        CF = self._CategoryFacet
        parent = Category.objects.create(
            name="Ritės", slug="rites", is_active=True
        )
        self.cat.parent = parent
        self.cat.save()
        CF.objects.create(
            category=parent, code="color", widget="checkbox", order=20,
        )
        CF.objects.create(
            category=parent, code="line-diameter", widget="range", order=10,
        )
        payload = self._payload()
        self.assertEqual(
            [f["code"] for f in payload["features"]],
            ["line-diameter", "color"],
        )

    def test_child_row_overrides_parent_same_code(self):
        """Vaiko eilutė toje pačioje kode laimi prieš tėvo."""
        CF = self._CategoryFacet
        parent = Category.objects.create(
            name="Ritės", slug="rites", is_active=True
        )
        self.cat.parent = parent
        self.cat.save()
        CF.objects.create(
            category=parent, code="color", widget="checkbox", order=10,
        )
        CF.objects.create(
            category=parent, code="line-diameter", widget="range", order=20,
        )
        CF.objects.create(
            category=self.cat, code="color", widget="checkbox", order=5,
        )
        payload = self._payload()
        feats = payload["features"]
        self.assertEqual(
            [f["code"] for f in feats], ["color", "line-diameter"]
        )


class RangeFilterTests(TestCase):
    """`range=<code>:<min>-<max>` — numeric overlap filtering (F4)."""

    def setUp(self):
        self.diameter = Feature.objects.create(
            code="line-diameter", name="Diameter",
            datatype="number", unit="mm", is_active=True,
        )
        self.v_thin = FeatureValue.objects.create(
            feature=self.diameter, value="0.26", value_num=Decimal("0.26"),
        )
        self.v_text = FeatureValue.objects.create(
            feature=self.diameter, value="0.28 mm",
        )
        self.weight = Feature.objects.create(
            code="weight", name="Weight",
            datatype="range", unit="g", is_active=True,
        )
        self.w_range = FeatureValue.objects.create(
            feature=self.weight, value="10-40-g",
            value_num=Decimal("10"), value_num_max=Decimal("40"),
        )

        self.p_thin = Product.objects.create(
            sku="RT-1", name="Thin", slug="rt-1", is_active=True,
        )
        ProductFeatureValue.objects.create(
            product=self.p_thin, feature=self.diameter,
            feature_value=self.v_thin,
        )
        self.p_text = Product.objects.create(
            sku="RT-2", name="Text", slug="rt-2", is_active=True,
        )
        ProductFeatureValue.objects.create(
            product=self.p_text, feature=self.diameter,
            feature_value=self.v_text,
        )
        self.p_rod = Product.objects.create(
            sku="RT-3", name="Rod", slug="rt-3", is_active=True,
        )
        ProductFeatureValue.objects.create(
            product=self.p_rod, feature=self.weight,
            feature_value=self.w_range,
        )

        # Option-value-backed numeric axis on a variant.
        self.spool = OptionType.objects.create(
            code="spool", name="Spool", datatype="number", is_active=True,
        )
        self.ov = OptionValue.objects.create(
            option_type=self.spool, code="2500", label="2500",
            value_num=Decimal("2500"),
        )
        self.p_reel = Product.objects.create(
            sku="RT-4", name="Reel", slug="rt-4", is_active=True,
        )
        self.reel_v = Variant.objects.create(
            product=self.p_reel, sku="RT-4-A", price_eur="1.00",
            is_active=True,
        )
        from catalog.models import VariantOptionValue

        VariantOptionValue.objects.create(
            variant=self.reel_v, option_type=self.spool,
            option_value=self.ov,
        )

    def _ids(self, range_param):
        qs, _ = apply_catalog_filters(
            qs=Product.objects.filter(is_active=True),
            range_filter=range_param,
        )
        return set(qs.values_list("id", flat=True))

    def test_scalar_value_num_in_range(self):
        self.assertEqual(self._ids("line-diameter:0.2-0.3"),
                         {self.p_thin.id, self.p_text.id})
        self.assertEqual(self._ids("line-diameter:0.3-0.4"), set())

    def test_range_value_overlap(self):
        # 10-40 g product matches a 20-50 window (partial overlap)...
        self.assertEqual(self._ids("weight:20-50"), {self.p_rod.id})
        # ...and a fully-inside window, but not a disjoint one.
        self.assertEqual(self._ids("weight:15-35"), {self.p_rod.id})
        self.assertEqual(self._ids("weight:41-60"), set())

    def test_open_bounds(self):
        self.assertIn(self.p_rod.id, self._ids("weight:20-"))
        self.assertIn(self.p_rod.id, self._ids("weight:-40"))
        self.assertNotIn(self.p_rod.id, self._ids("weight:-5"))

    def test_option_value_range(self):
        self.assertEqual(self._ids("spool:2000-3000"), {self.p_reel.id})
        self.assertEqual(self._ids("spool:3000-4000"), set())

    def test_multiple_ranges_combine(self):
        ids = self._ids("weight:20-50;line-diameter:0.2-0.3")
        self.assertEqual(ids, set())  # no product has both
        ids = self._ids("weight:20-50,line-diameter:0.2-0.3")
        self.assertEqual(ids, set())

    def test_invalid_range_param_raises_400(self):
        from ninja.errors import HttpError

        with self.assertRaises(HttpError):
            self._ids("weight:abc")
        with self.assertRaises(HttpError):
            self._ids("weight")


class SeedCategoryFacetsTests(TestCase):
    def _run_cmd(self, *args):
        from io import StringIO

        from django.core.management import call_command

        out = StringIO()
        call_command("seed_category_facets", *args, stdout=out)
        return out.getvalue()

    def setUp(self):
        from catalog.models import CategoryFacet

        self.cat = Category.objects.create(
            name="Spiningai", slug="spiningai", is_active=True
        )
        self.product = Product.objects.create(
            sku="SF-1", name="Rod", slug="sf-1",
            category=self.cat, is_active=True,
        )
        f_num = Feature.objects.create(
            code="casting-weight", name="CW",
            datatype="range", unit="g", is_active=True,
        )
        f_txt = Feature.objects.create(
            code="rod-model", name="Model", is_active=True,
        )
        ProductFeatureValue.objects.create(
            product=self.product, feature=f_num,
            feature_value=FeatureValue.objects.create(
                feature=f_num, value="10-40-g",
                value_num=Decimal("10"), value_num_max=Decimal("40"),
            ),
        )
        ProductFeatureValue.objects.create(
            product=self.product, feature=f_txt,
            feature_value=FeatureValue.objects.create(
                feature=f_txt, value="x-fast",
            ),
        )
        self._CF = CategoryFacet

    def test_dry_run_proposes_and_writes_nothing(self):
        out = self._run_cmd()
        self.assertIn("spiningai", out)
        self.assertIn("casting-weight", out)
        self.assertIn("range", out)
        self.assertIn("dry-run", out)
        self.assertFalse(self._CF.objects.exists())

    def test_apply_writes_rows(self):
        out = self._run_cmd("--apply")
        rows = {
            r.code: r for r in self._CF.objects.filter(category=self.cat)
        }
        self.assertEqual(rows["casting-weight"].widget, "range")
        self.assertEqual(rows["rod-model"].widget, "checkbox")
        self.assertEqual(
            [r.code for r in self._CF.objects.filter(category=self.cat)
             .order_by("order")],
            sorted(rows, key=lambda c: rows[c].order),
        )


class CategoryTranslationAdminLanguageTests(TestCase):
    @override_settings(LANGUAGE_CODE="lt", LANGUAGES=[("lt", "Lithuanian"), ("pl", "Polish"), ("en", "English")], SUPPORTED_LANGUAGE_CODES=["lt", "pl", "en"])
    def test_disabled_language_row_keeps_its_code(self):
        from catalog.admin import CategoryTranslationInline
        from catalog.models import Category, CategoryTranslation

        cat = Category.objects.create(name="Palapinės", slug="palapines-adm")
        lv = CategoryTranslation.objects.create(category=cat, language_code="lv", name="Teltis", slug="teltis-adm")
        form = CategoryTranslationInline.Form(instance=lv)
        codes = [c for c, _ in form.fields["language_code"].choices]
        self.assertEqual(codes, ["pl", "en", "lv"])
        self.assertIn('value="lv" selected', str(form["language_code"]))

        new = CategoryTranslationInline.Form()
        self.assertEqual([c for c, _ in new.fields["language_code"].choices], ["pl", "en"])
