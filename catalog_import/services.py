from __future__ import annotations

import hashlib
import json
from decimal import Decimal

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction
from django.utils import timezone
from django.utils.text import slugify
from pydantic import ValidationError

from catalog.models import (
    Brand,
    Category,
    Feature,
    FeatureValue,
    InventoryItem,
    Product,
    ProductFeatureValue,
    ProductImage,
    ProductOptionType,
    ProductTranslation,
    ProductVideo,
    TaxClass,
    Variant,
    VariantOptionValue,
    OptionType,
    OptionValue,
    Warehouse,
)

from .models import ImportItem, ImportRun
from .schemas import (
    CatalogImportEnvelope,
    ImportProductIn,
    ImportVariantIn,
    StockImportEnvelope,
    StockImportItemIn,
)


def file_sha256(payload_bytes: bytes) -> str:
    return hashlib.sha256(payload_bytes).hexdigest()


def create_run(
    *,
    kind: str,
    payload_bytes: bytes,
    file_name: str = "",
    triggered_by=None,
    options: dict | None = None,
) -> ImportRun:
    """Create an ImportRun. Same kind+sha256 file returns the existing run."""
    digest = file_sha256(payload_bytes)
    existing = (
        ImportRun.objects.filter(kind=kind, file_sha256=digest)
        .exclude(status=ImportRun.Status.FAILED)
        .order_by("-id")
        .first()
    )
    if existing:
        return existing
    return ImportRun.objects.create(
        kind=kind,
        file_name=file_name,
        file_sha256=digest,
        payload=payload_bytes.decode("utf-8", errors="replace"),
        options=options or {},
        triggered_by=(
            triggered_by if triggered_by and triggered_by.is_authenticated else None
        ),
    )


class _ItemError(Exception):
    pass


class _DryRunRollback(Exception):
    pass


def execute_run(run: ImportRun, *, triggered_by=None) -> ImportRun:
    """Parse payload, stage items, apply each in its own transaction."""
    run.status = ImportRun.Status.APPLYING
    run.triggered_by = triggered_by or run.triggered_by
    run.error_message = ""
    run.started_at = timezone.now()
    run.save(
        update_fields=["status", "triggered_by", "error_message", "started_at"]
    )
    ImportItem.objects.filter(run=run).delete()
    run.recount()

    try:
        data = json.loads(run.payload)
    except json.JSONDecodeError as exc:
        return _fail_run(run, f"Invalid JSON: {exc}")

    dry_run = bool(run.options.get("dry_run"))
    try:
        if run.kind == ImportRun.Kind.CATALOG:
            _process_catalog(run, data, dry_run=dry_run)
        else:
            _process_stock(run, data, dry_run=dry_run)
    except ValidationError as exc:
        return _fail_run(run, f"Schema error: {exc.errors(include_url=False)[:5]}")
    except (_ItemError, DjangoValidationError, TypeError, AttributeError) as exc:
        return _fail_run(run, f"Invalid file: {exc}")

    run.recount()
    run.finished_at = timezone.now()
    run.status = (
        ImportRun.Status.DONE
        if run.failed_items == 0
        else ImportRun.Status.DONE_WITH_ERRORS
    )
    if dry_run:
        run.status = ImportRun.Status.DONE
        run.error_message = "dry_run: no changes were written"
    run.save()
    return run


def _fail_run(run: ImportRun, message: str) -> ImportRun:
    run.status = ImportRun.Status.FAILED
    run.error_message = message
    run.finished_at = timezone.now()
    run.recount()
    run.save()
    return run


# ---------------- catalog items ----------------


def _process_catalog(run: ImportRun, data: dict, *, dry_run: bool) -> None:
    env = CatalogImportEnvelope(**data)
    if env.format_version != 1:
        raise _ItemError(f"unsupported format_version {env.format_version}")
    run.source = env.source
    run.mode = env.mode if env.mode in (ImportRun.Mode.UPSERT, ImportRun.Mode.FULL_SYNC) else ImportRun.Mode.UPSERT
    run.save(update_fields=["source", "mode"])

    seen_product_ids: set[int] = set()
    for row_no, raw in enumerate(env.items, start=1):
        row = ImportItem.objects.create(run=run, row_no=row_no, payload=raw)
        try:
            item = ImportProductIn(**raw)
            row.external_code = item.code
            row.save(update_fields=["external_code"])
        except ValidationError as exc:
            _mark_error(row, _format_validation(exc))
            continue
        _apply_one(run, row, lambda: _apply_product(run, item, seen_product_ids, row), dry_run)

    if run.mode == ImportRun.Mode.FULL_SYNC and not dry_run:
        _deactivate_absent(run, seen_product_ids)
    run.recount()
    run.save(update_fields=["total_items", "ok_items", "failed_items", "skipped_items"])


def _process_stock(run: ImportRun, data: dict, *, dry_run: bool) -> None:
    env = StockImportEnvelope(**data)
    if env.format_version != 1:
        raise _ItemError(f"unsupported format_version {env.format_version}")
    run.source = env.source
    run.save(update_fields=["source"])

    try:
        warehouse = Warehouse.objects.get(code=env.warehouse_code)
    except Warehouse.DoesNotExist:
        raise _ItemError(f"unknown warehouse_code '{env.warehouse_code}'")

    for row_no, raw in enumerate(env.items, start=1):
        row = ImportItem.objects.create(run=run, row_no=row_no, payload=raw)
        try:
            item = StockImportItemIn(**raw)
            row.external_code = item.code
            row.save(update_fields=["external_code"])
        except ValidationError as exc:
            _mark_error(row, _format_validation(exc))
            continue
        _apply_one(run, row, lambda: _apply_stock(row, item, warehouse), dry_run)
    run.recount()
    run.save(update_fields=["total_items", "ok_items", "failed_items", "skipped_items"])


def _apply_one(run: ImportRun, row: ImportItem, fn, dry_run: bool) -> None:
    try:
        with transaction.atomic():
            fn()
            if dry_run:
                raise _DryRunRollback
        if dry_run:
            row.status = ImportItem.Status.SKIPPED
        else:
            row.status = ImportItem.Status.OK
        row.save(update_fields=["status"])
    except _DryRunRollback:
        row.status = ImportItem.Status.SKIPPED
        row.save(update_fields=["status"])
    except _ItemError as exc:
        _mark_error(row, str(exc))
    except Exception as exc:  # noqa: BLE001 - keep run going on unexpected row errors
        _mark_error(row, f"unexpected: {exc}")


def _mark_error(row: ImportItem, message: str) -> None:
    row.status = ImportItem.Status.ERROR
    row.error = message[:4000]
    row.save(update_fields=["status", "error"])


def _format_validation(exc: ValidationError) -> str:
    parts = []
    for err in exc.errors(include_url=False)[:5]:
        loc = ".".join(str(p) for p in err.get("loc", ()))
        parts.append(f"{loc}: {err.get('msg')}")
    return "; ".join(parts)


def _deactivate_absent(run: ImportRun, seen_ids: set[int]) -> None:
    qs = Product.objects.filter(import_source=run.source).exclude(id__in=seen_ids)
    qs.update(is_active=False)


# ---------------- product upsert ----------------


def _apply_product(run: ImportRun, item: ImportProductIn, seen_ids: set[int], row: ImportItem) -> None:
    lt = item.translations.get("lt")
    if not lt or not lt.name.strip():
        raise _ItemError("translations.lt.name is required")

    tax_class = TaxClass.objects.filter(code=item.tax_class).first()
    if tax_class is None:
        raise _ItemError(f"unknown tax_class '{item.tax_class}'")

    product = _match_product(item, run.source)
    created = product is None
    if created:
        product = Product(sku=item.code)
    product.import_source = run.source
    product.import_code = item.code
    product.product_group_code = item.product_group_code or ""
    product.tax_class = tax_class
    product.is_active = item.is_active
    product.weight_g = item.weight_g
    product.length_cm = item.length_cm
    product.width_cm = item.width_cm
    product.height_cm = item.height_cm
    product.name = lt.name.strip()
    product.description = lt.description or ""
    product.seo_title = lt.seo_title or ""
    product.seo_description = lt.seo_description or ""
    product.seo_keywords = lt.seo_keywords or ""
    product.additional_info = item.additional_info or {}
    if created or not product.slug:
        product.slug = _unique_slug(Product, lt.slug or lt.name)
    product.save()

    seen_ids.add(product.id)
    _sync_translations(product, item)
    _sync_taxonomy(product, item)
    _sync_features(product, item)
    _sync_images(product, item)
    _sync_videos(product, item)
    variants = _sync_variants(product, item, run.source)
    _sync_stock(product, variants, item)

    row.product_id = product.id
    if variants:
        row.variant_id = variants[0].id
    row.save(update_fields=["product_id", "variant_id"])


def _match_product(item: ImportProductIn, source: str) -> Product | None:
    product = Product.objects.filter(
        import_source=source, import_code=item.code
    ).first()
    if product:
        return product
    product = Product.objects.filter(sku=item.code).first()
    if product is None:
        return None
    if product.import_code and product.import_code != item.code:
        raise _ItemError(
            f"sku '{item.code}' belongs to import_code '{product.import_code}' "
            f"(source '{product.import_source}')"
        )
    if product.import_source and product.import_source != source:
        raise _ItemError(
            f"sku '{item.code}' owned by source '{product.import_source}', "
            f"incoming source '{source}' has conflicting data"
        )
    return product


def _unique_slug(model, base: str) -> str:
    slug = slugify(base) or "product"
    candidate, i = slug, 2
    while model.objects.filter(slug=candidate).exists():
        candidate = f"{slug}-{i}"
        i += 1
    return candidate


def _sync_translations(product: Product, item: ImportProductIn) -> None:
    for lang, tr in item.translations.items():
        if lang == "lt":
            continue
        if not tr.name.strip():
            continue
        tr_obj, created = ProductTranslation.objects.get_or_create(
            product=product, language_code=lang, defaults={"name": tr.name.strip()}
        )
        tr_obj.name = tr.name.strip()
        tr_obj.description = tr.description or ""
        tr_obj.seo_title = tr.seo_title or ""
        tr_obj.seo_description = tr.seo_description or ""
        tr_obj.seo_keywords = tr.seo_keywords or ""
        if created or not tr_obj.slug:
            slug_base = tr.slug or tr.name
            tr_obj.slug = _unique_translation_slug(product, lang, slug_base)
        tr_obj.save()


def _unique_translation_slug(product: Product, lang: str, base: str) -> str:
    slug = slugify(base) or "product"
    candidate, i = slug, 2
    qs = ProductTranslation.objects.filter(language_code=lang, slug=candidate)
    while qs.exclude(product=product).exists():
        candidate = f"{slug}-{i}"
        i += 1
        qs = ProductTranslation.objects.filter(language_code=lang, slug=candidate)
    return candidate


def _sync_taxonomy(product: Product, item: ImportProductIn) -> None:
    if item.category:
        category = Category.objects.filter(slug=item.category).first()
        if category is None:
            raise _ItemError(f"unknown category slug '{item.category}'")
        product.category = category
    if item.brand_code:
        brand = Brand.objects.filter(slug=item.brand_code).first() or Brand.objects.filter(
            import_code=item.brand_code
        ).first()
        if brand is None:
            brand, _ = Brand.objects.get_or_create(
                slug=item.brand_code,
                defaults={"name": item.brand_code.replace("-", " ").title()},
            )
        product.brand = brand
    product.save()


def _sync_features(product: Product, item: ImportProductIn) -> None:
    feature_ids = []
    for f in item.features:
        feature, _ = Feature.objects.get_or_create(
            code=f.code, defaults={"name": f.code.replace("-", " ").title()}
        )
        for lang, name in f.name_translations.items():
            feature.translations.update_or_create(
                language_code=lang,
                defaults={"name": name},
            )
        value, _ = FeatureValue.objects.get_or_create(
            feature=feature, value=f.value
        )
        for lang, label in f.value_translations.items():
            value.translations.update_or_create(
                language_code=lang, defaults={"value": label}
            )
        ProductFeatureValue.objects.filter(
            product=product, feature=feature
        ).exclude(feature_value=value).delete()
        ProductFeatureValue.objects.get_or_create(
            product=product, feature=feature, feature_value=value
        )
        feature_ids.append(feature.id)
    ProductFeatureValue.objects.filter(product=product).exclude(
        feature_id__in=feature_ids
    ).delete()


def _dispatch_image_ingest(obj: ProductImage) -> None:
    """Queue download of external image_url into object storage (after commit)."""
    if obj.image or not obj.image_url:
        return
    if obj.ingest_status == ProductImage.INGEST_PENDING:
        return
    if obj.ingest_status != ProductImage.INGEST_PENDING:
        obj.ingest_status = ProductImage.INGEST_PENDING
        obj.save(update_fields=["ingest_status"])
    pk = obj.pk

    def _dispatch(pk=pk):
        from catalog.tasks import ingest_product_image_task
        from config.celery import safe_delay
        safe_delay(ingest_product_image_task, pk)

    transaction.on_commit(_dispatch)


def _sync_images(product: Product, item: ImportProductIn) -> None:
    for img in item.images:
        if not img.url:
            continue
        obj, _ = ProductImage.objects.get_or_create(
            product=product, image_url=img.url,
            defaults={"alt_text": img.alt, "sort_order": img.sort_order},
        )
        changed = False
        if img.alt and obj.alt_text != img.alt:
            obj.alt_text = img.alt
            changed = True
        if obj.sort_order != img.sort_order:
            obj.sort_order = img.sort_order
            changed = True
        if changed:
            obj.save(update_fields=["alt_text", "sort_order"])
        _dispatch_image_ingest(obj)


def _sync_videos(product: Product, item: ImportProductIn) -> None:
    """Tiekėjo video URL'ai (YouTube ir pan.). Dedup pagal url; trūkstami – ištrinami."""
    keep_ids = []
    for index, video in enumerate(item.videos):
        if not video.url:
            continue
        sort_order = video.sort_order or index
        obj, _ = ProductVideo.objects.get_or_create(
            product=product, url=video.url,
            defaults={
                "video_type": video.type or ProductVideo.Type.PRODUCT,
                "language_code": video.language or "",
                "sort_order": sort_order,
            },
        )
        changed = False
        if video.type and obj.video_type != video.type:
            obj.video_type = video.type
            changed = True
        if video.language and obj.language_code != video.language:
            obj.language_code = video.language
            changed = True
        if obj.sort_order != sort_order:
            obj.sort_order = sort_order
            changed = True
        if changed:
            obj.save(update_fields=["video_type", "language_code", "sort_order"])
        keep_ids.append(obj.id)
    ProductVideo.objects.filter(product=product).exclude(id__in=keep_ids).delete()


def _sync_variants(product: Product, item: ImportProductIn, source: str) -> list[Variant]:
    specs = item.variants
    if not specs:
        specs = [
            ImportVariantIn(
                code=item.code,
                product_group_code=item.product_group_code,
                ean=item.ean,
                price_net=item.price_net,
                cost_net=item.cost_net,
                weight_g=item.weight_g,
                length_cm=item.length_cm,
                width_cm=item.width_cm,
                height_cm=item.height_cm,
                is_active=item.is_active,
                stock=item.stock,
            )
        ]
    out = []
    for spec in specs:
        variant = _match_variant(product, spec, source)
        created = variant is None
        if created:
            variant = Variant(product=product, sku=spec.code)
        variant.import_source = source
        variant.import_code = spec.code
        variant.product_group_code = spec.product_group_code or item.product_group_code
        if spec.ean:
            conflict = (
                Variant.objects.filter(barcode=spec.ean).exclude(pk=variant.pk).first()
            )
            if conflict and conflict.product_id != product.id:
                raise _ItemError(f"ean '{spec.ean}' already used by variant '{conflict.sku}'")
            variant.barcode = spec.ean
        variant.is_active = spec.is_active
        price_net = spec.price_net if spec.price_net is not None else item.price_net
        if price_net is not None:
            variant.price_eur = Decimal(price_net)
        cost_net = spec.cost_net if spec.cost_net is not None else item.cost_net
        if cost_net is not None:
            variant.cost_eur = Decimal(cost_net)
        variant.weight_g = spec.weight_g or item.weight_g
        variant.length_cm = spec.length_cm or item.length_cm
        variant.width_cm = spec.width_cm or item.width_cm
        variant.height_cm = spec.height_cm or item.height_cm
        variant.save()
        _sync_variant_options(product, variant, spec.options, item.option_labels)
        _sync_variant_images(product, variant, spec)
        out.append(variant)
    return out


def _sync_variant_images(product: Product, variant: Variant, spec: ImportVariantIn) -> None:
    for img in spec.images:
        if not img.url:
            continue
        obj, _ = ProductImage.objects.get_or_create(
            product=product, image_url=img.url,
            defaults={"alt_text": img.alt, "sort_order": img.sort_order},
        )
        if not obj.variants.filter(pk=variant.pk).exists():
            obj.variants.add(variant)
        _dispatch_image_ingest(obj)


def _match_variant(product: Product, spec: ImportVariantIn, source: str) -> Variant | None:
    variant = Variant.objects.filter(
        import_source=source, import_code=spec.code
    ).first()
    if variant and variant.product_id != product.id:
        raise _ItemError(
            f"variant code '{spec.code}' belongs to product '{variant.product.sku}'"
        )
    if variant:
        return variant
    variant = Variant.objects.filter(sku=spec.code).first()
    if variant is None:
        return None
    if variant.product_id != product.id:
        raise _ItemError(
            f"sku '{spec.code}' belongs to product '{variant.product.sku}'"
        )
    if variant.import_code and variant.import_code != spec.code:
        raise _ItemError(
            f"sku '{spec.code}' owned by import_code '{variant.import_code}'"
        )
    return variant


def _sync_variant_options(
    product: Product,
    variant: Variant,
    options: dict[str, str],
    option_labels: dict[str, dict[str, str]] | None = None,
) -> None:
    if not options:
        return
    labels = option_labels or {}
    value_ids = []
    for type_code, value_code in options.items():
        opt_type, _ = OptionType.objects.get_or_create(
            code=type_code,
            defaults={"name": type_code.replace("-", " ").replace("_", " ").title()},
        )
        ProductOptionType.objects.get_or_create(product=product, option_type=opt_type)
        label = (labels.get(type_code) or {}).get(value_code) or value_code.replace(
            "-", " "
        ).replace("_", " ").title()
        opt_value, created = OptionValue.objects.get_or_create(
            option_type=opt_type,
            code=value_code,
            defaults={"label": label},
        )
        if not created and opt_value.label != label:
            opt_value.label = label
            opt_value.save(update_fields=["label"])
        VariantOptionValue.objects.update_or_create(
            variant=variant,
            option_type=opt_type,
            defaults={"option_value": opt_value},
        )
        value_ids.append(opt_value.id)
    VariantOptionValue.objects.filter(variant=variant).exclude(
        option_value_id__in=value_ids
    ).delete()


def _sync_stock(product: Product, variants: list[Variant], item: ImportProductIn) -> None:
    if not variants:
        return
    # product-level stock applies to the default/first variant
    if item.stock:
        _write_stock(variants[0], item.stock)
    for spec, variant in zip(item.variants, variants):
        if spec.stock:
            _write_stock(variant, spec.stock)


def _write_stock(variant: Variant, stock: dict[str, int]) -> None:
    for wh_code, qty in stock.items():
        warehouse = Warehouse.objects.filter(code=wh_code).first()
        if warehouse is None:
            raise _ItemError(f"unknown warehouse_code '{wh_code}'")
        if qty < 0:
            raise _ItemError(f"negative qty for '{variant.sku}' in '{wh_code}'")
        inv, _ = InventoryItem.objects.get_or_create(
            variant=variant, warehouse=warehouse
        )
        if inv.qty_on_hand != qty:
            inv.qty_on_hand = qty
            inv.save(update_fields=["qty_on_hand", "updated_at"])


# ---------------- stock items ----------------


def _apply_stock(row: ImportItem, item: StockImportItemIn, warehouse: Warehouse) -> None:
    if item.qty < 0:
        raise _ItemError(f"negative qty for '{item.code}'")
    variant = Variant.objects.filter(sku=item.code).first()
    if variant is None:
        raise _ItemError(f"unknown variant sku '{item.code}'")
    inv, _ = InventoryItem.objects.get_or_create(variant=variant, warehouse=warehouse)
    if inv.qty_on_hand != item.qty:
        inv.qty_on_hand = item.qty
        inv.save(update_fields=["qty_on_hand", "updated_at"])
    row.variant_id = variant.id
    row.save(update_fields=["variant_id"])
