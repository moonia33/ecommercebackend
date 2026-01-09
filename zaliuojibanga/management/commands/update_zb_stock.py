from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import Iterable
from urllib.request import Request, urlopen

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.db.models import Sum

from catalog.models import InventoryItem, Product, Variant, Warehouse


DEFAULT_URL = "https://zaliojibanga.lt/integrations/services/stocks.php?key=3fWgWWXyTa9OCXG8"
DEFAULT_WAREHOUSE_CODE = "zalioji_banga"

# Batch size for DB work.
BATCH_SIZE = 500


def _text(el: ET.Element | None) -> str:
    if el is None:
        return ""
    return (el.text or "").strip()


def _parse_int(value: str) -> int | None:
    s = (value or "").strip()
    if not s:
        return None
    try:
        return int(s)
    except Exception:
        return None


@dataclass(frozen=True)
class StockItem:
    sku: str
    barcode: str
    qty: int


def _iter_items(xml_stream) -> Iterable[StockItem]:
    # Stream-parse large XML feeds.
    context = ET.iterparse(xml_stream, events=("end",))
    for _event, elem in context:
        if elem.tag != "item":
            continue

        sku = _text(elem.find("code"))
        barcode = _text(elem.find("ean"))
        qty_raw = _text(elem.find("qty"))
        qty = _parse_int(qty_raw)

        elem.clear()

        if qty is None:
            continue
        if not sku and not barcode:
            continue

        yield StockItem(sku=sku, barcode=barcode, qty=qty)


class Command(BaseCommand):
    help = "Atnaujina Zalioji banga likučius (qty) pagal SKU ir/arba EAN (barcode)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--url",
            default=None,
            help="XML feed URL (jei nenurodyta – naudoja settings.ZB_STOCKS_FEED_URL arba default).",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Nieko nekeičia DB, tik parodo suvestinę.",
        )
        parser.add_argument(
            "--limit",
            type=int,
            default=None,
            help="Maksimalus įrašų skaičius iš feed (debug).",
        )

    def handle(self, *args, **options):
        url = options.get("url") or getattr(
            settings, "ZB_STOCKS_FEED_URL", "") or DEFAULT_URL
        dry_run: bool = bool(options.get("dry_run"))
        limit_opt = options.get("limit")
        limit: int | None = int(limit_opt) if limit_opt is not None else None
        if limit is not None and limit < 1:
            raise CommandError("--limit turi būti teigiamas skaičius")

        warehouse = Warehouse.objects.filter(
            code=DEFAULT_WAREHOUSE_CODE).first()
        if not warehouse:
            raise CommandError(
                f"Warehouse '{DEFAULT_WAREHOUSE_CODE}' nerastas (spec sako, kad jau turi būti sukurtas)."
            )

        self.stdout.write(f"Skaitau XML: {url}")

        updated_inventory = 0
        created_inventory = 0
        updated_variants = 0
        updated_products = 0
        not_found = 0
        conflicts = 0

        affected_product_ids: set[int] = set()

        def process_batch(batch: list[StockItem]):
            nonlocal updated_inventory, created_inventory, updated_variants, not_found, conflicts
            if not batch:
                return

            skus = {b.sku for b in batch if b.sku}
            barcodes = {b.barcode for b in batch if b.barcode}

            variants_by_sku = {v.sku: v for v in Variant.objects.filter(
                sku__in=skus)} if skus else {}
            variants_by_barcode = (
                {v.barcode: v for v in Variant.objects.filter(
                    barcode__in=barcodes)} if barcodes else {}
            )

            # Resolve items -> variants (SKU preferred, then barcode)
            resolved: list[tuple[Variant, int]] = []
            for item in batch:
                variant = None
                if item.sku:
                    variant = variants_by_sku.get(item.sku)
                if variant is None and item.barcode:
                    variant = variants_by_barcode.get(item.barcode)

                if variant is None:
                    not_found += 1
                    continue

                # If both sku+barcode exist and point to different variants, prefer SKU but count conflict.
                if item.sku and item.barcode:
                    v2 = variants_by_barcode.get(item.barcode)
                    if v2 is not None and v2.pk != variant.pk:
                        conflicts += 1

                resolved.append((variant, item.qty))

            if dry_run:
                # In dry-run we only count what would be updated.
                updated_inventory += len(resolved)
                updated_variants += len(resolved)
                for v, _qty in resolved:
                    affected_product_ids.add(v.product_id)
                return

            # DB write: keep it transactional per batch.
            with transaction.atomic():
                variant_ids = [v.pk for v, _ in resolved]
                inv_qs = InventoryItem.objects.filter(
                    warehouse=warehouse, variant_id__in=variant_ids)
                inv_by_variant_id = {inv.variant_id: inv for inv in inv_qs}

                to_create: list[InventoryItem] = []
                to_update: list[InventoryItem] = []

                for variant, qty in resolved:
                    inv = inv_by_variant_id.get(variant.pk)
                    if inv is None:
                        inv = InventoryItem(
                            variant=variant,
                            warehouse=warehouse,
                            qty_on_hand=qty,
                            qty_reserved=0,
                            cost_eur=getattr(variant, "cost_eur", None),
                        )
                        to_create.append(inv)
                        created_inventory += 1
                    else:
                        inv.qty_on_hand = qty
                        to_update.append(inv)

                    affected_product_ids.add(variant.product_id)

                if to_create:
                    InventoryItem.objects.bulk_create(
                        to_create, ignore_conflicts=True)

                if to_update:
                    InventoryItem.objects.bulk_update(
                        to_update, ["qty_on_hand"])  # reserved stays as-is

                updated_inventory += len(resolved)
                updated_variants += len(resolved)

        req = Request(url, headers={"User-Agent": "django_ecommerce/zb-stock"})
        with urlopen(req, timeout=60) as resp:
            if getattr(resp, "status", 200) >= 400:
                raise CommandError(
                    f"HTTP klaida: {getattr(resp, 'status', 'unknown')}")

            batch: list[StockItem] = []
            processed = 0
            for item in _iter_items(resp):
                batch.append(item)
                processed += 1

                if limit is not None and processed >= limit:
                    break

                if len(batch) >= BATCH_SIZE:
                    process_batch(batch)
                    batch = []

            if batch:
                process_batch(batch)

        if affected_product_ids:
            updated_products = len(affected_product_ids)

        self.stdout.write(
            self.style.SUCCESS(
                "Likučių atnaujinimas baigtas. "
                f"inventory_updated={updated_inventory}, inventory_created={created_inventory}, "
                f"variants_updated={updated_variants}, products_updated={updated_products}, "
                f"not_found={not_found}, conflicts={conflicts}"
                + (" (dry-run)" if dry_run else "")
            )
        )
