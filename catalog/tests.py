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
    FeatureValueTranslation,
    OptionType,
    OptionTypeTranslation,
    OptionValue,
    OptionValueTranslation,
    Product,
    ProductTranslation,
    TaxClass,
)
from catalog.services.query_filters import apply_catalog_filters


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
