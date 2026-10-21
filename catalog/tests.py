import datetime

from django.test import TestCase

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
