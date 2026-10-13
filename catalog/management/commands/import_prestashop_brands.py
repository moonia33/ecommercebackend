from __future__ import annotations

import json
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils.text import slugify

from catalog.models import Brand, Product


DEFAULT_SOURCE_PATH = "docs/ps_manufacturer.json"


def _unique_slug(base_slug: str, *, existing: set[str], stable_suffix: str) -> str:
    base = (base_slug or "").strip("-")
    if not base:
        base = "brand"

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


class Command(BaseCommand):
    help = "One-time import of PrestaShop manufacturers as Brand entries."

    def add_arguments(self, parser):
        parser.add_argument(
            "--source",
            type=str,
            default=DEFAULT_SOURCE_PATH,
            help=f"Path to Presta manufacturer export JSON (default: {DEFAULT_SOURCE_PATH}).",
        )
        parser.add_argument(
            "--replace-existing",
            action="store_true",
            help="Replace existing brands before import (detaches Product.brand first).",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Do not write changes, only print summary.",
        )

    def handle(self, *args, **options):
        source_path = Path(str(options.get("source") or DEFAULT_SOURCE_PATH))
        replace_existing = bool(options.get("replace_existing"))
        dry_run = bool(options.get("dry_run"))

        if not source_path.exists():
            raise CommandError(f"Source file not found: {source_path}")

        try:
            payload = json.loads(source_path.read_text(encoding="utf-8"))
        except Exception as exc:
            raise CommandError(f"Cannot parse JSON: {exc}") from exc

        if not isinstance(payload, list):
            raise CommandError("Unexpected export format: top-level must be a JSON array.")

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
            raise CommandError("Cannot find phpMyAdmin table payload with `data` rows.")

        rows = table.get("data", [])

        prepared: list[tuple[str, str]] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            ext_id = str(row.get("id_manufacturer", "")).strip()
            name = str(row.get("name", "")).strip()
            if not ext_id or not name:
                continue
            prepared.append((ext_id, name))

        if dry_run:
            self.stdout.write(self.style.WARNING(f"Dry-run: would import brands={len(prepared)}."))
            return

        created = 0
        updated = 0

        with transaction.atomic():
            if replace_existing:
                Product.objects.update(brand=None)
                Brand.objects.all().delete()

            used_slugs = set(Brand.objects.values_list("slug", flat=True))

            for ext_id, name in prepared:
                existing = Brand.objects.filter(external_presta_id=ext_id).first()
                if existing is None:
                    slug_hint = slugify(name) or f"brand-{ext_id}"
                    slug = _unique_slug(slug_hint, existing=used_slugs, stable_suffix=ext_id)
                    Brand.objects.create(
                        external_presta_id=ext_id,
                        name=name,
                        slug=slug,
                        is_active=True,
                    )
                    created += 1
                else:
                    existing.name = name
                    existing.is_active = True
                    existing.save(update_fields=["name", "is_active"])
                    used_slugs.add(existing.slug)
                    updated += 1

        self.stdout.write(
            self.style.SUCCESS(
                f"Presta brands import done. created={created}, updated={updated}, total={len(prepared)}."
            )
        )
