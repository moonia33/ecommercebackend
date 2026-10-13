from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils.text import slugify

from api.i18n import get_supported_language_codes
from catalog.models import (
    Brand,
    Category,
    InventoryItem,
    Product,
    ProductTranslation,
    TaxClass,
    Variant,
    Warehouse,
)
from catalog.richtext import normalize_richtext_to_markdown


DEFAULT_SOURCE_DIR = "docs/prestashop_migration"
BASE_LANG_ID = "1"  # lt
BASE_SHOP_ID = "1"
LANG_CODE_BY_PRESTA_LANG_ID = {
    "1": "lt",
    "2": "lv",
    "3": "pl",
    "4": "et",
    "6": "en",
}


def _load_table_rows(path: Path) -> list[dict]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise CommandError(f"Unexpected JSON format in {path}")
    table = next(
        (
            x
            for x in payload
            if isinstance(x, dict)
            and x.get("type") == "table"
            and isinstance(x.get("data"), list)
        ),
        None,
    )
    if table is None:
        raise CommandError(f"Cannot find table data in {path}")
    return table["data"]


def _parse_decimal(raw: str | None, default: str = "0.00") -> Decimal:
    s = (raw or "").strip().replace(",", ".")
    if not s:
        return Decimal(default)
    try:
        return Decimal(s)
    except Exception:
        return Decimal(default)


def _unique_slug(base_slug: str, *, existing: set[str], stable_suffix: str) -> str:
    base = (base_slug or "").strip("-")
    if not base:
        base = "product"

    if base not in existing:
        existing.add(base)
        return base

    candidate = f"{base}-{stable_suffix}".strip("-")
    if candidate not in existing:
        existing.add(candidate)
        return candidate

    idx = 2
    while True:
        candidate = f"{base}-{stable_suffix}-{idx}".strip("-")
        if candidate not in existing:
            existing.add(candidate)
            return candidate
        idx += 1


def _unique_translation_slug(
    language_code: str,
    base_slug: str,
    *,
    existing_by_lang: dict[str, set[str]],
    stable_suffix: str,
) -> str:
    bucket = existing_by_lang.setdefault(language_code, set())
    return _unique_slug(base_slug, existing=bucket, stable_suffix=stable_suffix)


def _to_markdown_from_html(value: str | None) -> str:
    return normalize_richtext_to_markdown(value or "", input_format="html").markdown


class Command(BaseCommand):
    help = "Import PrestaShop standard products (no combinations), active and with supplier."

    def add_arguments(self, parser):
        parser.add_argument("--source-dir", type=str, default=DEFAULT_SOURCE_DIR)
        parser.add_argument("--dry-run", action="store_true")
        parser.add_argument("--limit", type=int, default=None)
        parser.add_argument("--only-active", action="store_true", default=True)
        parser.add_argument("--include-without-supplier", action="store_true", default=False)
        parser.add_argument(
            "--warehouse-code",
            type=str,
            default="presta_import",
            help="Warehouse code for imported stock.",
        )
        parser.add_argument(
            "--tax-class-code",
            type=str,
            default="standard",
            help="Tax class code assigned to imported products.",
        )

    def handle(self, *args, **options):
        source_dir = Path(str(options.get("source_dir") or DEFAULT_SOURCE_DIR))
        dry_run = bool(options.get("dry_run"))
        limit = options.get("limit")
        only_active = bool(options.get("only_active"))
        include_without_supplier = bool(options.get("include_without_supplier"))
        warehouse_code = str(options.get("warehouse_code") or "presta_import").strip()
        tax_class_code = str(options.get("tax_class_code") or "standard").strip()
        supported_language_codes = set(get_supported_language_codes())

        required_files = [
            "ps_product.json",
            "ps_product_lang.json",
            "ps_product_attribute.json",
            "ps_stock_available.json",
        ]
        for fname in required_files:
            if not (source_dir / fname).exists():
                raise CommandError(f"Missing required source file: {source_dir / fname}")

        products_rows = _load_table_rows(source_dir / "ps_product.json")
        product_lang_rows = _load_table_rows(source_dir / "ps_product_lang.json")
        combination_rows = _load_table_rows(source_dir / "ps_product_attribute.json")
        stock_rows = _load_table_rows(source_dir / "ps_stock_available.json")

        has_combinations: set[str] = {str(r.get("id_product", "")).strip() for r in combination_rows}

        lang_rows_by_product: dict[str, dict[str, dict]] = {}
        for row in product_lang_rows:
            if str(row.get("id_shop", "")).strip() != BASE_SHOP_ID:
                continue
            lang_id = str(row.get("id_lang", "")).strip()
            if lang_id not in LANG_CODE_BY_PRESTA_LANG_ID:
                continue
            product_id = str(row.get("id_product", "")).strip()
            if not product_id:
                continue
            lang_rows_by_product.setdefault(product_id, {})[lang_id] = row

        stock_by_product: dict[str, int] = {}
        for row in stock_rows:
            if str(row.get("id_product_attribute", "")).strip() not in {"0", ""}:
                continue
            product_id = str(row.get("id_product", "")).strip()
            if not product_id:
                continue
            qty = int(_parse_decimal(str(row.get("quantity", "0")), default="0"))
            stock_by_product[product_id] = qty

        category_by_presta = {
            str(c.external_presta_id): c
            for c in Category.objects.exclude(external_presta_id__isnull=True).exclude(external_presta_id="")
        }
        brand_by_presta = {
            str(b.external_presta_id): b
            for b in Brand.objects.exclude(external_presta_id__isnull=True).exclude(external_presta_id="")
        }

        filtered: list[dict] = []
        skipped_inactive = skipped_no_supplier = skipped_has_combinations = skipped_no_lang = 0
        for row in products_rows:
            product_id = str(row.get("id_product", "")).strip()
            if not product_id:
                continue
            if only_active and str(row.get("active", "0")).strip() != "1":
                skipped_inactive += 1
                continue
            if not include_without_supplier and str(row.get("id_supplier", "0")).strip() in {"0", ""}:
                skipped_no_supplier += 1
                continue
            if product_id in has_combinations:
                skipped_has_combinations += 1
                continue
            if BASE_LANG_ID not in lang_rows_by_product.get(product_id, {}):
                skipped_no_lang += 1
                continue
            filtered.append(row)
            if limit and len(filtered) >= int(limit):
                break

        if dry_run:
            self.stdout.write(
                self.style.WARNING(
                    "Dry-run product import. "
                    f"selected={len(filtered)}, skipped_inactive={skipped_inactive}, "
                    f"skipped_no_supplier={skipped_no_supplier}, skipped_has_combinations={skipped_has_combinations}, "
                    f"skipped_no_lang={skipped_no_lang}"
                )
            )
            return

        tax_class = TaxClass.objects.filter(code=tax_class_code).first()
        if not tax_class:
            raise CommandError(f"Tax class '{tax_class_code}' not found.")

        warehouse, _ = Warehouse.objects.get_or_create(
            code=warehouse_code,
            defaults={"name": "Presta import", "country_code": "LT", "is_active": True},
        )

        used_product_slugs = set(Product.objects.values_list("slug", flat=True))
        used_translation_slugs_by_lang: dict[str, set[str]] = {}
        for lang_code, slug in ProductTranslation.objects.values_list("language_code", "slug"):
            lang_code = (lang_code or "").strip().lower()
            slug = (slug or "").strip()
            if not lang_code or not slug:
                continue
            used_translation_slugs_by_lang.setdefault(lang_code, set()).add(slug)

        created_products = updated_products = created_variants = updated_stock = 0
        created_translations = updated_translations = 0
        missing_category_map = missing_brand_map = 0

        with transaction.atomic():
            for row in filtered:
                product_id = str(row.get("id_product", "")).strip()
                by_lang = lang_rows_by_product.get(product_id, {})
                lang_row = by_lang[BASE_LANG_ID]

                name = (lang_row.get("name") or "").strip() or f"Product {product_id}"
                description_html = (lang_row.get("description") or "").strip()
                description_short_html = (lang_row.get("description_short") or "").strip()
                description = _to_markdown_from_html(description_html)
                if not description and description_short_html:
                    description = _to_markdown_from_html(description_short_html)
                seo_title = (lang_row.get("meta_title") or "").strip()
                seo_description = (lang_row.get("meta_description") or "").strip()
                seo_keywords = (lang_row.get("meta_keywords") or "").strip()

                reference = (row.get("reference") or "").strip()
                sku = reference or f"PS-{product_id}"
                if len(sku) > 64:
                    sku = sku[:64]

                link_rewrite = (lang_row.get("link_rewrite") or "").strip()
                slug_hint = link_rewrite or slugify(name) or f"product-{product_id}"

                category = category_by_presta.get(str(row.get("id_category_default", "")).strip())
                if category is None:
                    missing_category_map += 1

                brand = brand_by_presta.get(str(row.get("id_manufacturer", "")).strip())
                if str(row.get("id_manufacturer", "")).strip() not in {"", "0"} and brand is None:
                    missing_brand_map += 1

                price = _parse_decimal(str(row.get("price", "0.00")), default="0.00")
                cost = _parse_decimal(str(row.get("wholesale_price", "0.00")), default="0.00")
                qty = max(0, int(stock_by_product.get(product_id, 0)))

                product = Product.objects.filter(external_presta_id=product_id).first()
                if product is None:
                    # Backfill mapping when DB already has a product with same SKU.
                    product = Product.objects.filter(sku=sku).first()
                if product is None:
                    slug = _unique_slug(slug_hint, existing=used_product_slugs, stable_suffix=product_id)
                    product = Product.objects.create(
                        external_presta_id=product_id,
                        sku=sku,
                        name=name,
                        slug=slug,
                        description=description,
                        seo_title=seo_title,
                        seo_description=seo_description,
                        seo_keywords=seo_keywords,
                        brand=brand,
                        category=category,
                        tax_class=tax_class,
                        is_active=True,
                    )
                    created_products += 1
                else:
                    used_product_slugs.add(product.slug)
                    if not product.external_presta_id:
                        product.external_presta_id = product_id
                    product.sku = sku
                    product.name = name
                    product.description = description
                    product.seo_title = seo_title
                    product.seo_description = seo_description
                    product.seo_keywords = seo_keywords
                    product.brand = brand
                    product.category = category
                    product.tax_class = tax_class
                    product.is_active = True
                    product.save(
                        update_fields=[
                            "sku",
                            "external_presta_id",
                            "name",
                            "description",
                            "seo_title",
                            "seo_description",
                            "seo_keywords",
                            "brand",
                            "category",
                            "tax_class",
                            "is_active",
                        ]
                    )
                    updated_products += 1

                for presta_lang_id, tr_row in by_lang.items():
                    if presta_lang_id == BASE_LANG_ID:
                        continue
                    language_code = LANG_CODE_BY_PRESTA_LANG_ID.get(presta_lang_id)
                    if not language_code:
                        continue
                    if supported_language_codes and language_code not in supported_language_codes:
                        continue

                    tr_name = (tr_row.get("name") or "").strip()
                    tr_description_html = (tr_row.get("description") or "").strip()
                    tr_description_short_html = (tr_row.get("description_short") or "").strip()
                    tr_description = _to_markdown_from_html(tr_description_html)
                    if not tr_description and tr_description_short_html:
                        tr_description = _to_markdown_from_html(tr_description_short_html)
                    tr_seo_title = (tr_row.get("meta_title") or "").strip()
                    tr_seo_description = (tr_row.get("meta_description") or "").strip()
                    tr_seo_keywords = (tr_row.get("meta_keywords") or "").strip()

                    link_rewrite_tr = (tr_row.get("link_rewrite") or "").strip()
                    tr_slug_hint = link_rewrite_tr or slugify(tr_name) or f"product-{product_id}-{language_code}"

                    tr = ProductTranslation.objects.filter(
                        product=product,
                        language_code=language_code,
                    ).first()
                    if tr is None:
                        tr_slug = _unique_translation_slug(
                            language_code,
                            tr_slug_hint,
                            existing_by_lang=used_translation_slugs_by_lang,
                            stable_suffix=product_id,
                        )
                        ProductTranslation.objects.create(
                            product=product,
                            language_code=language_code,
                            name=tr_name,
                            slug=tr_slug,
                            description=tr_description,
                            seo_title=tr_seo_title,
                            seo_description=tr_seo_description,
                            seo_keywords=tr_seo_keywords,
                        )
                        created_translations += 1
                    else:
                        used_translation_slugs_by_lang.setdefault(language_code, set()).add(tr.slug)
                        tr.name = tr_name
                        tr.description = tr_description
                        tr.seo_title = tr_seo_title
                        tr.seo_description = tr_seo_description
                        tr.seo_keywords = tr_seo_keywords
                        tr.save(
                            update_fields=[
                                "name",
                                "description",
                                "seo_title",
                                "seo_description",
                                "seo_keywords",
                            ]
                        )
                        updated_translations += 1

                variant = product.variants.filter(external_presta_combination_id__isnull=True).first()
                if variant is None:
                    # Backfill mapping when variant already exists globally by SKU.
                    variant = Variant.objects.filter(sku=sku).first()
                if variant is None:
                    variant = Variant.objects.create(
                        product=product,
                        external_presta_combination_id=None,
                        sku=sku,
                        barcode=(row.get("ean13") or "").strip()[:64],
                        price_eur=price,
                        cost_eur=cost,
                        is_active=True,
                    )
                    created_variants += 1
                else:
                    variant.sku = sku
                    variant.barcode = (row.get("ean13") or "").strip()[:64]
                    variant.price_eur = price
                    variant.cost_eur = cost
                    variant.is_active = True
                    variant.save(update_fields=["sku", "barcode", "price_eur", "cost_eur", "is_active"])

                inv, _ = InventoryItem.objects.get_or_create(
                    variant=variant,
                    warehouse=warehouse,
                    defaults={"qty_on_hand": qty, "qty_reserved": 0, "cost_eur": cost},
                )
                if inv.qty_on_hand != qty or (inv.cost_eur or Decimal("0.00")) != cost:
                    inv.qty_on_hand = qty
                    inv.cost_eur = cost
                    inv.save(update_fields=["qty_on_hand", "cost_eur"])
                    updated_stock += 1

        self.stdout.write(
            self.style.SUCCESS(
                "Presta products import done. "
                f"created_products={created_products}, updated_products={updated_products}, "
                f"created_variants={created_variants}, updated_stock={updated_stock}, "
                f"created_translations={created_translations}, updated_translations={updated_translations}, "
                f"missing_category_map={missing_category_map}, missing_brand_map={missing_brand_map}, "
                f"selected={len(filtered)}"
            )
        )
