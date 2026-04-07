from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError

from api.i18n import get_default_language_code, normalize_language_code
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
    ProductGroup,
    ProductGroupTranslation,
    Product,
    ProductTranslation,
)


class Command(BaseCommand):
    help = "Backfill missing catalog translation rows from base fields."

    def add_arguments(self, parser):
        parser.add_argument(
            "--language-code",
            type=str,
            default=None,
            help="Language code to backfill (default: settings LANGUAGE_CODE base).")
        parser.add_argument(
            "--categories",
            action="store_true",
            default=False,
            help="Backfill category translations.")
        parser.add_argument(
            "--brands",
            action="store_true",
            default=False,
            help="Backfill brand translations.")
        parser.add_argument(
            "--product-groups",
            action="store_true",
            default=False,
            help="Backfill product group translations.")
        parser.add_argument(
            "--features",
            action="store_true",
            default=False,
            help="Backfill feature + feature value translations.")
        parser.add_argument(
            "--option-types",
            action="store_true",
            default=False,
            help="Backfill option type + option value translations.")
        parser.add_argument(
            "--products",
            action="store_true",
            default=False,
            help="Backfill product translations.")
        parser.add_argument(
            "--dry-run",
            action="store_true",
            default=False,
            help="Do not write to DB; only print what would change.")

    def handle(self, *args, **options):
        language_code = normalize_language_code(options.get("language_code")) or get_default_language_code()
        if not language_code:
            raise CommandError("Unable to determine language_code")

        do_categories = bool(options.get("categories"))
        do_brands = bool(options.get("brands"))
        do_product_groups = bool(options.get("product_groups"))
        do_features = bool(options.get("features"))
        do_option_types = bool(options.get("option_types"))
        do_products = bool(options.get("products"))
        dry_run = bool(options.get("dry_run"))

        if (
            not do_categories
            and not do_brands
            and not do_product_groups
            and not do_features
            and not do_option_types
            and not do_products
        ):
            do_categories = True
            do_brands = True
            do_product_groups = True
            do_features = True
            do_option_types = True
            do_products = True

        created_categories = 0
        created_brands = 0
        created_product_groups = 0
        created_features = 0
        created_feature_values = 0
        created_option_types = 0
        created_option_values = 0
        created_products = 0
        skipped_conflict = 0

        if do_categories:
            for c in Category.objects.all().only(
                "id",
                "name",
                "slug",
                "description",
                "seo_title",
                "seo_description",
                "seo_keywords",
            ):
                exists = CategoryTranslation.objects.filter(category_id=int(c.id), language_code=language_code).exists()
                if exists:
                    continue

                slug = (c.slug or "").strip()
                if not slug:
                    slug = f"category-{int(c.id)}"

                unique_slug = self._unique_category_slug(language_code=language_code, slug=slug, category_id=int(c.id))
                if not unique_slug:
                    skipped_conflict += 1
                    continue

                if dry_run:
                    created_categories += 1
                    continue

                CategoryTranslation.objects.create(
                    category_id=int(c.id),
                    language_code=language_code,
                    name=(c.name or ""),
                    slug=unique_slug,
                    description=(c.description or ""),
                    seo_title=(getattr(c, "seo_title", "") or ""),
                    seo_description=(getattr(c, "seo_description", "") or ""),
                    seo_keywords=(getattr(c, "seo_keywords", "") or ""),
                )
                created_categories += 1

        if do_brands:
            for b in Brand.objects.all().only(
                "id",
                "name",
                "slug",
                "description",
                "seo_title",
                "seo_description",
                "seo_keywords",
            ):
                exists = BrandTranslation.objects.filter(brand_id=int(b.id), language_code=language_code).exists()
                if exists:
                    continue

                slug = (b.slug or "").strip()
                if not slug:
                    slug = f"brand-{int(b.id)}"

                unique_slug = self._unique_brand_slug(language_code=language_code, slug=slug, brand_id=int(b.id))
                if not unique_slug:
                    skipped_conflict += 1
                    continue

                if dry_run:
                    created_brands += 1
                    continue

                BrandTranslation.objects.create(
                    brand_id=int(b.id),
                    language_code=language_code,
                    name=(b.name or ""),
                    slug=unique_slug,
                    description=(getattr(b, "description", "") or ""),
                    seo_title=(getattr(b, "seo_title", "") or ""),
                    seo_description=(getattr(b, "seo_description", "") or ""),
                    seo_keywords=(getattr(b, "seo_keywords", "") or ""),
                )
                created_brands += 1

        if do_product_groups:
            for g in ProductGroup.objects.all().only(
                "id",
                "name",
                "slug",
                "description",
                "seo_title",
                "seo_description",
                "seo_keywords",
            ):
                exists = ProductGroupTranslation.objects.filter(
                    product_group_id=int(g.id),
                    language_code=language_code,
                ).exists()
                if exists:
                    continue

                slug = (getattr(g, "slug", "") or "").strip()
                if not slug:
                    slug = f"product-group-{int(g.id)}"

                unique_slug = self._unique_product_group_slug(
                    language_code=language_code,
                    slug=slug,
                    product_group_id=int(g.id),
                )
                if not unique_slug:
                    skipped_conflict += 1
                    continue

                if dry_run:
                    created_product_groups += 1
                    continue

                ProductGroupTranslation.objects.create(
                    product_group_id=int(g.id),
                    language_code=language_code,
                    name=(g.name or ""),
                    slug=unique_slug,
                    description=(getattr(g, "description", "") or ""),
                    seo_title=(getattr(g, "seo_title", "") or ""),
                    seo_description=(getattr(g, "seo_description", "") or ""),
                    seo_keywords=(getattr(g, "seo_keywords", "") or ""),
                )
                created_product_groups += 1

        if do_features:
            for f in Feature.objects.all().only("id", "name"):
                exists = FeatureTranslation.objects.filter(feature_id=int(f.id), language_code=language_code).exists()
                if not exists:
                    if dry_run:
                        created_features += 1
                    else:
                        FeatureTranslation.objects.create(
                            feature_id=int(f.id),
                            language_code=language_code,
                            name=(f.name or ""),
                        )
                        created_features += 1

            for v in FeatureValue.objects.all().only("id", "value"):
                exists = FeatureValueTranslation.objects.filter(
                    feature_value_id=int(v.id),
                    language_code=language_code,
                ).exists()
                if exists:
                    continue

                if dry_run:
                    created_feature_values += 1
                    continue

                FeatureValueTranslation.objects.create(
                    feature_value_id=int(v.id),
                    language_code=language_code,
                    value=(v.value or ""),
                )
                created_feature_values += 1

        if do_option_types:
            for t in OptionType.objects.all().only("id", "name"):
                exists = OptionTypeTranslation.objects.filter(
                    option_type_id=int(t.id),
                    language_code=language_code,
                ).exists()
                if not exists:
                    if dry_run:
                        created_option_types += 1
                    else:
                        OptionTypeTranslation.objects.create(
                            option_type_id=int(t.id),
                            language_code=language_code,
                            name=(t.name or ""),
                        )
                        created_option_types += 1

            for v in OptionValue.objects.all().only("id", "label"):
                exists = OptionValueTranslation.objects.filter(
                    option_value_id=int(v.id),
                    language_code=language_code,
                ).exists()
                if exists:
                    continue

                if dry_run:
                    created_option_values += 1
                    continue

                OptionValueTranslation.objects.create(
                    option_value_id=int(v.id),
                    language_code=language_code,
                    label=(v.label or ""),
                )
                created_option_values += 1

        if do_products:
            for p in Product.objects.all().only(
                "id",
                "name",
                "slug",
                "description",
                "seo_title",
                "seo_description",
                "seo_keywords",
            ):
                exists = ProductTranslation.objects.filter(product_id=int(p.id), language_code=language_code).exists()
                if exists:
                    continue

                slug = (getattr(p, "slug", "") or "").strip()
                if not slug:
                    slug = f"product-{int(p.id)}"

                unique_slug = self._unique_product_slug(
                    language_code=language_code,
                    slug=slug,
                    product_id=int(p.id),
                )
                if not unique_slug:
                    skipped_conflict += 1
                    continue

                if dry_run:
                    created_products += 1
                    continue

                ProductTranslation.objects.create(
                    product_id=int(p.id),
                    language_code=language_code,
                    name=(p.name or ""),
                    slug=unique_slug,
                    description=(getattr(p, "description", "") or ""),
                    seo_title=(getattr(p, "seo_title", "") or ""),
                    seo_description=(getattr(p, "seo_description", "") or ""),
                    seo_keywords=(getattr(p, "seo_keywords", "") or ""),
                )
                created_products += 1

        self.stdout.write(
            self.style.SUCCESS(
                "Done. "
                f"language_code={language_code}, "
                f"created_categories={created_categories}, "
                f"created_brands={created_brands}, "
                f"created_product_groups={created_product_groups}, "
                f"created_features={created_features}, "
                f"created_feature_values={created_feature_values}, "
                f"created_option_types={created_option_types}, "
                f"created_option_values={created_option_values}, "
                f"created_products={created_products}, "
                f"skipped_conflict={skipped_conflict}, "
                f"dry_run={dry_run}"
            )
        )

    def _unique_category_slug(self, *, language_code: str, slug: str, category_id: int) -> str | None:
        slug = (slug or "").strip()
        if not slug:
            return None

        qs = CategoryTranslation.objects.filter(language_code=language_code, slug=slug)
        if not qs.exists():
            return slug

        qs = qs.exclude(category_id=int(category_id))
        if not qs.exists():
            return slug

        suffix = f"-{int(category_id)}"
        max_len = CategoryTranslation._meta.get_field("slug").max_length
        base = slug
        if max_len and len(base) + len(suffix) > int(max_len):
            base = base[: int(max_len) - len(suffix)]
        candidate = f"{base}{suffix}"

        if CategoryTranslation.objects.filter(language_code=language_code, slug=candidate).exists():
            return None
        return candidate

    def _unique_brand_slug(self, *, language_code: str, slug: str, brand_id: int) -> str | None:
        slug = (slug or "").strip()
        if not slug:
            return None

        qs = BrandTranslation.objects.filter(language_code=language_code, slug=slug)
        if not qs.exists():
            return slug

        qs = qs.exclude(brand_id=int(brand_id))
        if not qs.exists():
            return slug

        suffix = f"-{int(brand_id)}"
        max_len = BrandTranslation._meta.get_field("slug").max_length
        base = slug
        if max_len and len(base) + len(suffix) > int(max_len):
            base = base[: int(max_len) - len(suffix)]
        candidate = f"{base}{suffix}"

        if BrandTranslation.objects.filter(language_code=language_code, slug=candidate).exists():
            return None
        return candidate

    def _unique_product_group_slug(self, *, language_code: str, slug: str, product_group_id: int) -> str | None:
        slug = (slug or "").strip()
        if not slug:
            return None

        qs = ProductGroupTranslation.objects.filter(language_code=language_code, slug=slug)
        if not qs.exists():
            return slug

        qs = qs.exclude(product_group_id=int(product_group_id))
        if not qs.exists():
            return slug

        suffix = f"-{int(product_group_id)}"
        max_len = ProductGroupTranslation._meta.get_field("slug").max_length
        base = slug
        if max_len and len(base) + len(suffix) > int(max_len):
            base = base[: int(max_len) - len(suffix)]
        candidate = f"{base}{suffix}"

        if ProductGroupTranslation.objects.filter(language_code=language_code, slug=candidate).exists():
            return None
        return candidate

    def _unique_product_slug(self, *, language_code: str, slug: str, product_id: int) -> str | None:
        slug = (slug or "").strip()
        if not slug:
            return None

        qs = ProductTranslation.objects.filter(language_code=language_code, slug=slug)
        if not qs.exists():
            return slug

        qs = qs.exclude(product_id=int(product_id))
        if not qs.exists():
            return slug

        suffix = f"-{int(product_id)}"
        max_len = ProductTranslation._meta.get_field("slug").max_length
        base = slug
        if max_len and len(base) + len(suffix) > int(max_len):
            base = base[: int(max_len) - len(suffix)]
        candidate = f"{base}{suffix}"

        if ProductTranslation.objects.filter(language_code=language_code, slug=candidate).exists():
            return None
        return candidate
