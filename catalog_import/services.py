from __future__ import annotations

import hashlib
import json
import logging
import re
import threading
from contextlib import contextmanager
from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import connection, transaction
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
    VariantFeatureValue,
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


logger = logging.getLogger(__name__)


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
    touched_product_ids: set[int] = set()
    try:
        from search.signals import suppress_search_sync

        with suppress_search_sync(), _suppress_image_ingest():
            if run.kind == ImportRun.Kind.CATALOG:
                touched_product_ids = _process_catalog(run, data, dry_run=dry_run)
            else:
                touched_product_ids = _process_stock(run, data, dry_run=dry_run)
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

    if not dry_run:
        _schedule_search_sync(touched_product_ids)
    return run


def _start_async_apply(run: ImportRun) -> None:
    """Start a non-blocking apply for `apply_async=true` requests.

    Celery first (unless task_always_eager, which would run inline and
    block the HTTP request); a daemon thread is the fallback — needed
    both for eager dev mode and for a dead broker. The run row is already
    committed by create_run before this is called.
    """
    from config.celery import safe_delay

    if not getattr(settings, "CELERY_TASK_ALWAYS_EAGER", False):
        from .tasks import apply_import_run_task

        if safe_delay(apply_import_run_task, run.id) is not None:
            return

    def _apply(run_id=run.id):
        try:
            execute_run(ImportRun.objects.get(id=run_id))
        except Exception:  # noqa: BLE001 - surfaced via run status/logs
            logger.exception("async apply of import run %s failed", run_id)
        finally:
            connection.close()

    threading.Thread(target=_apply, daemon=True).start()


def _schedule_search_sync(product_ids: set[int]) -> None:
    """Reindex only the products an import run touched.

    Per-row signal syncs are suppressed during the run, so this single
    deferred sync is the only index traffic. Deferred via
    transaction.on_commit like the search signals — a no-op inside tests
    (TestCase never commits). Runs via Celery when a broker is up, inline
    otherwise; failures are logged, not raised.
    """
    if not product_ids:
        return
    from search.services import search_available

    if not search_available():
        return

    ordered = sorted(int(pid) for pid in product_ids)
    chunks = [ordered[i : i + 500] for i in range(0, len(ordered), 500)]

    def _dispatch(chunks=chunks):
        from config.celery import safe_delay
        from search.tasks import sync_products_task

        for chunk in chunks:
            try:
                if safe_delay(sync_products_task, chunk) is not None:
                    continue
            except Exception:  # noqa: BLE001 - import must not fail over search
                logger.exception("failed to enqueue search sync")
            try:
                from search.services import index_products

                index_products(chunk)
            except Exception:  # noqa: BLE001 - degrade to stale index + log
                logger.exception("inline search sync failed")

    transaction.on_commit(_dispatch)


def _fail_run(run: ImportRun, message: str) -> ImportRun:
    run.status = ImportRun.Status.FAILED
    run.error_message = message
    run.finished_at = timezone.now()
    run.recount()
    run.save()
    return run


# ---------------- catalog items ----------------


def _process_catalog(run: ImportRun, data: dict, *, dry_run: bool) -> set[int]:
    env = CatalogImportEnvelope(**data)
    if env.format_version not in (1, 2):
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

    if not dry_run and run.source:
        # Regrouped šeimos: perkelti variantai palieka tuščius produktus
        # (import'o valdomi "husk'ai" – PDP be perkamų variantų). Jie
        # deaktyvuojami nepriklausomai nuo mode (upsert ir full_sync).
        emptied = Product.objects.filter(
            import_source=run.source, variants__isnull=True
        ).exclude(is_active=False)
        emptied_ids = set(emptied.values_list("id", flat=True))
        if emptied_ids:
            emptied.update(is_active=False)
            seen_product_ids |= emptied_ids

    if run.mode == ImportRun.Mode.FULL_SYNC and not dry_run:
        # Deactivated products also need reindexing (doc removal).
        seen_product_ids |= _deactivate_absent(run, seen_product_ids)
    run.recount()
    run.save(update_fields=["total_items", "ok_items", "failed_items", "skipped_items"])
    return seen_product_ids


def _process_stock(run: ImportRun, data: dict, *, dry_run: bool) -> set[int]:
    env = StockImportEnvelope(**data)
    if env.format_version not in (1, 2):
        raise _ItemError(f"unsupported format_version {env.format_version}")
    run.source = env.source
    run.save(update_fields=["source"])

    try:
        warehouse = Warehouse.objects.get(code=env.warehouse_code)
    except Warehouse.DoesNotExist:
        raise _ItemError(f"unknown warehouse_code '{env.warehouse_code}'")

    touched_product_ids: set[int] = set()
    for row_no, raw in enumerate(env.items, start=1):
        row = ImportItem.objects.create(run=run, row_no=row_no, payload=raw)
        try:
            item = StockImportItemIn(**raw)
            row.external_code = item.code
            row.save(update_fields=["external_code"])
        except ValidationError as exc:
            _mark_error(row, _format_validation(exc))
            continue
        product_id = _apply_one(
            run, row, lambda: _apply_stock(row, item, warehouse), dry_run
        )
        if product_id and not dry_run:
            touched_product_ids.add(product_id)
    run.recount()
    run.save(update_fields=["total_items", "ok_items", "failed_items", "skipped_items"])
    return touched_product_ids


def _apply_one(run: ImportRun, row: ImportItem, fn, dry_run: bool):
    try:
        with transaction.atomic():
            result = fn()
            if dry_run:
                raise _DryRunRollback
        if dry_run:
            row.status = ImportItem.Status.SKIPPED
        else:
            row.status = ImportItem.Status.OK
        row.save(update_fields=["status"])
        return result
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


def _deactivate_absent(run: ImportRun, seen_ids: set[int]) -> set[int]:
    qs = Product.objects.filter(import_source=run.source).exclude(id__in=seen_ids)
    deactivated_ids = set(qs.values_list("id", flat=True))
    qs.update(is_active=False)
    return deactivated_ids


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
    product.is_part = item.is_part
    if created or not product.slug:
        product.slug = _unique_slug(Product, lt.slug or lt.name)
    product.save()

    seen_ids.add(product.id)
    _sync_translations(product, item)
    _sync_taxonomy(product, item)
    _sync_features(product, item)
    img_keep_ids = _sync_images(product, item)
    _sync_videos(product, item)
    variants, img_keep_ids = _sync_variants(
        product, item, run.source, img_keep_ids
    )
    # Feed'as — tiesa nuotrauku sudetiui: pasenusias eilutes (nepatekusias i
    # feed'a) triname kartu su failais. Kai feed'as visai neduoda nuotrauku,
    # nieko netriname — saugiklis nuo tuscio/gadingo feed'o.
    if img_keep_ids:
        from catalog.services.image_cleanup import bulk_delete_product_images

        stale = (
            ProductImage.objects.filter(product=product)
            .exclude(id__in=img_keep_ids)
            .values_list("id", flat=True)
        )
        bulk_delete_product_images(list(stale))
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
    from api.i18n import get_supported_language_codes

    supported = set(get_supported_language_codes())
    for lang, tr in item.translations.items():
        if lang == "lt":
            continue
        if lang not in supported:
            logger.warning(
                "Skipping translation for unsupported language %r (product %s)",
                lang,
                product.pk,
            )
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


_PARTS_CATEGORY_SLUG = "atsargines-dalys"
_PARTS_CATEGORY_NAME = "Atsarginės dalys"


def _parts_category() -> Category:
    """Dedikuota atsarginių dalių kategorija (list template)."""
    category, _ = Category.objects.get_or_create(
        slug=_PARTS_CATEGORY_SLUG,
        defaults={
            "name": _PARTS_CATEGORY_NAME,
            "list_template": Category.ListTemplate.LIST,
        },
    )
    if category.list_template != Category.ListTemplate.LIST:
        category.list_template = Category.ListTemplate.LIST
        category.save(update_fields=["list_template"])
    return category


def _sync_taxonomy(product: Product, item: ImportProductIn) -> None:
    if item.is_part:
        # Dalių produktai visada patenka į 'atsargines-dalys', nepriklausomai
        # nuo payload `category`. Be is_part -> kategorija atstatoma į payload.
        product.category = _parts_category()
    elif item.category:
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


_DASH_UNDERSCORE = re.compile(r"(?<=[A-Za-z])[-_]+|[-_]+(?=[A-Za-z])")


def _humanize(code: str) -> str:
    """'material_fabric'/'100-polyester' -> 'material fabric'/'100 polyester'.

    Dashes between digits are kept ('10-30' stays a range). Underscores
    always become spaces.
    """
    return _DASH_UNDERSCORE.sub(" ", code.replace("_", " ")).strip()


def _first_translation(translations: dict[str, str]) -> str:
    """Pick the display string: 'en' first, then any non-empty value."""
    en = (translations.get("en") or "").strip()
    if en:
        return en
    for t in translations.values():
        t = (t or "").strip()
        if t:
            return t
    return ""


def _upsert_feature_value(f: ImportFeatureIn) -> tuple[Feature, FeatureValue]:
    # Canonical code: feeds send both 'ball_bearings' and 'ball-bearings'
    # for the same attribute; normalize so they share one Feature row.
    code = f.code.strip().lower().replace("_", "-")
    humanized = _humanize(code).title()
    feature, created = Feature.objects.get_or_create(
        code=code, defaults={"name": humanized}
    )
    name_from_feed = _first_translation(f.name_translations)
    # Refresh the display name only when it is still the raw/auto code —
    # names curated in the admin are never overwritten by imports.
    if name_from_feed and (
        created
        or not feature.name.strip()
        or feature.name in {f.code, code, humanized}
    ):
        feature.name = name_from_feed
        feature.save(update_fields=["name"])
    for lang, name in f.name_translations.items():
        feature.translations.update_or_create(
            language_code=lang,
            defaults={"name": name},
        )
    feature_dirty = []
    if f.datatype and feature.datatype != f.datatype:
        feature.datatype = f.datatype
        feature_dirty.append("datatype")
    if f.unit and feature.unit != f.unit:
        feature.unit = f.unit
        feature_dirty.append("unit")
    if feature_dirty:
        feature.save(update_fields=feature_dirty)
    value, v_created = FeatureValue.objects.get_or_create(
        feature=feature, value=f.value
    )
    label_from_feed = _first_translation(f.value_translations) or _humanize(
        f.value
    )
    value_dirty = []
    if value.value_num != f.value_num:
        value.value_num = f.value_num
        value_dirty.append("value_num")
    if value.value_num_max != f.value_num_max:
        value.value_num_max = f.value_num_max
        value_dirty.append("value_num_max")
    if f.unit and value.unit != f.unit:
        value.unit = f.unit
        value_dirty.append("unit")
    if label_from_feed and (
        v_created or not value.label.strip() or value.label == value.value
    ):
        value.label = label_from_feed
        value_dirty.append("label")
    if value_dirty:
        value.save(update_fields=value_dirty)
    for lang, label in f.value_translations.items():
        value.translations.update_or_create(
            language_code=lang, defaults={"value": label}
        )
    return feature, value


def _sync_features(product: Product, item: ImportProductIn) -> None:
    feature_ids = []
    for f in item.features:
        feature, value = _upsert_feature_value(f)
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


def _sync_variant_features(variant: Variant, spec: ImportVariantIn) -> None:
    feature_ids = []
    for f in spec.features:
        feature, value = _upsert_feature_value(f)
        VariantFeatureValue.objects.filter(
            variant=variant, feature=feature
        ).exclude(feature_value=value).delete()
        VariantFeatureValue.objects.get_or_create(
            variant=variant, feature=feature, feature_value=value
        )
        feature_ids.append(feature.id)
    VariantFeatureValue.objects.filter(variant=variant).exclude(
        feature_id__in=feature_ids
    ).delete()


_image_ingest_state = threading.local()


@contextmanager
def _suppress_image_ingest():
    """Mute per-row image downloads during import runs.

    Importing a supplier catalog must not block on tens of thousands of
    upstream HTTP downloads — rows stay INGEST_PENDING and are filled by
    `ingest_pending_images` (cron/worker/CLI) afterwards.
    """
    prev = getattr(_image_ingest_state, "suppressed", False)
    _image_ingest_state.suppressed = True
    try:
        yield
    finally:
        _image_ingest_state.suppressed = prev


def _dispatch_image_ingest(obj: ProductImage) -> None:
    """Queue download of external image_url into object storage (after commit)."""
    if obj.image or not obj.image_url:
        return
    if obj.ingest_status != ProductImage.INGEST_PENDING:
        obj.ingest_status = ProductImage.INGEST_PENDING
        obj.save(update_fields=["ingest_status"])
    if getattr(_image_ingest_state, "suppressed", False):
        return
    pk = obj.pk

    def _dispatch(pk=pk):
        from catalog.tasks import ingest_product_image_task
        from config.celery import safe_delay
        safe_delay(ingest_product_image_task, pk)

    transaction.on_commit(_dispatch)


def _sync_images(product: Product, item: ImportProductIn) -> list[int]:
    keep_ids: list[int] = []
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
        keep_ids.append(obj.id)
        _dispatch_image_ingest(obj)
    return keep_ids


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


def _sync_variants(
    product: Product,
    item: ImportProductIn,
    source: str,
    img_keep_ids: list[int] | None = None,
) -> tuple[list[Variant], list[int]]:
    img_keep_ids = img_keep_ids if img_keep_ids is not None else []
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
        variant.supplier_code = spec.supplier_code or ""
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
        variant.pack_unit = spec.pack_unit or ""
        variant.pack_quantity = spec.pack_quantity or 0
        variant.trade_pack_qty = spec.trade_pack_qty or 0
        variant.pack_barcode = spec.pack_barcode or ""
        variant.sold_as_pack = bool(spec.sold_as_pack)
        if spec.name.strip():
            variant.name = spec.name.strip()
        variant.pack_price_eur = (
            Decimal(spec.pack_price) if spec.pack_price is not None else None
        )
        variant.pack_purchase_price_eur = (
            Decimal(spec.pack_purchase_price)
            if spec.pack_purchase_price is not None
            else None
        )
        variant.save()
        _sync_variant_options(
            product, variant, spec.options, item.option_labels,
            item.option_value_meta,
        )
        _sync_variant_features(variant, spec)
        img_keep_ids.extend(_sync_variant_images(product, variant, spec))
        out.append(variant)
    # ProductOptionType eilutės tik tiems tipams, kuriuos dar naudoja
    # bent vienas variantas (options n -> 0 atveju tipai pranyksta).
    used_type_ids = VariantOptionValue.objects.filter(
        variant__product=product
    ).values_list("option_type_id", flat=True).distinct()
    ProductOptionType.objects.filter(product=product).exclude(
        option_type_id__in=used_type_ids
    ).delete()
    return out, img_keep_ids


def _sync_variant_images(product: Product, variant: Variant, spec: ImportVariantIn) -> list[int]:
    keep_ids: list[int] = []
    for img in spec.images:
        if not img.url:
            continue
        obj, _ = ProductImage.objects.get_or_create(
            product=product, image_url=img.url,
            defaults={"alt_text": img.alt, "sort_order": img.sort_order},
        )
        if not obj.variants.filter(pk=variant.pk).exists():
            obj.variants.add(variant)
        keep_ids.append(obj.id)
        _dispatch_image_ingest(obj)
    # Feed'as — tiesa ir varianto image ryšiams: atjungiame pasenusias
    # (pvz. Fox galerijos kopijas ant visų variantų). Tuščias spec.images
    # nekeičia ryšių — tas pats saugiklis kaip ir produkto lygmenyje.
    if keep_ids or spec.images:
        stale_links = variant.variant_images.exclude(id__in=keep_ids)
        if stale_links.exists():
            variant.variant_images.remove(*stale_links)
    return keep_ids


def _match_variant(product: Product, spec: ImportVariantIn, source: str) -> Variant | None:
    variant = Variant.objects.filter(
        import_source=source, import_code=spec.code
    ).first()
    if variant and variant.product_id != product.id:
        # Tas pats importo šaltinis valdo abu produktus – feed'as
        # pergrupavo variantus (pvz. Korda kabliukų dydžių sujungimas).
        # Leidžiame perkėlimą: varianto id išlieka, order history ir
        # inventoriaus eilutės nesidaro.
        variant.product = product
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
    option_value_meta: dict | None = None,
) -> None:
    if not options:
        # Produkto options dingo (n -> 0 importas): išvalom senas
        # varianto option eilutes, kad filtruose nesimatytų.
        VariantOptionValue.objects.filter(variant=variant).delete()
        return
    labels = option_labels or {}
    meta = option_value_meta or {}
    value_ids = []
    for raw_type_code, value_code in options.items():
        type_code = raw_type_code.strip().lower().replace("_", "-")
        opt_type, _ = OptionType.objects.get_or_create(
            code=type_code,
            defaults={"name": _humanize(type_code).title()},
        )
        value_meta = (
            (meta.get(type_code) or meta.get(raw_type_code) or {}).get(value_code)
        )
        if value_meta and value_meta.unit and opt_type.unit != value_meta.unit:
            opt_type.unit = value_meta.unit
            opt_type.save(update_fields=["unit"])
        ProductOptionType.objects.get_or_create(product=product, option_type=opt_type)
        label = (
            (labels.get(type_code) or labels.get(raw_type_code) or {}).get(value_code)
            or _humanize(value_code).title()
        )
        opt_value, created = OptionValue.objects.get_or_create(
            option_type=opt_type,
            code=value_code,
            defaults={"label": label},
        )
        dirty = []
        if not created and opt_value.label != label:
            opt_value.label = label
            dirty.append("label")
        if value_meta:
            if opt_value.value_num != value_meta.num:
                opt_value.value_num = value_meta.num
                dirty.append("value_num")
            if opt_value.value_num_max != value_meta.num_max:
                opt_value.value_num_max = value_meta.num_max
                dirty.append("value_num_max")
            if value_meta.unit and opt_value.unit != value_meta.unit:
                opt_value.unit = value_meta.unit
                dirty.append("unit")
        if dirty:
            opt_value.save(update_fields=dirty)
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


def _apply_stock(row: ImportItem, item: StockImportItemIn, warehouse: Warehouse) -> int:
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
    return variant.product_id
