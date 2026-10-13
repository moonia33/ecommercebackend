from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils.text import slugify

from catalog.models import Category, CategoryTranslation
from catalog.richtext import normalize_richtext_to_markdown


DEFAULT_SOURCE_PATH = "docs/prestashop_category_for_import.json"
DEFAULT_ALLOWED_LANG_IDS = ("1", "2", "3", "4", "6")  # lt, lv, pl, et, en
DEFAULT_LANG_PRIORITY = ("1", "6", "2", "3", "4")  # prefer lt, then en
DEFAULT_EXCLUDED_CATEGORY_IDS = ("1", "2")
LANG_CODE_BY_PRESTA_LANG_ID = {
    "1": "lt",
    "2": "lv",
    "3": "pl",
    "4": "et",
    "6": "en",
}


@dataclass(frozen=True)
class CategorySource:
    id_category: str
    id_parent: str
    level_depth: int
    base_row: dict
    rows_by_lang: dict[str, dict]


def _parse_ids(raw: str) -> set[str]:
    return {part.strip() for part in (raw or "").split(",") if part.strip()}


def _to_int(raw: str, *, default: int = 0) -> int:
    try:
        return int(str(raw).strip())
    except Exception:
        return default


def _unique_slug(base_slug: str, *, existing: set[str], stable_suffix: str) -> str:
    base = (base_slug or "").strip("-")
    if not base:
        base = "category"

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
    help = (
        "One-time import of PrestaShop categories from exported JSON while preserving parent-child tree."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--source",
            type=str,
            default=DEFAULT_SOURCE_PATH,
            help=f"Path to Presta export JSON (default: {DEFAULT_SOURCE_PATH}).",
        )
        parser.add_argument(
            "--allowed-lang-ids",
            type=str,
            default=",".join(DEFAULT_ALLOWED_LANG_IDS),
            help="Comma-separated Presta language ids to include (default: 1,2,3,4,6).",
        )
        parser.add_argument(
            "--lang-priority",
            type=str,
            default=",".join(DEFAULT_LANG_PRIORITY),
            help="Comma-separated language priority used to pick a single stored category name/slug.",
        )
        parser.add_argument(
            "--exclude-category-ids",
            type=str,
            default=",".join(DEFAULT_EXCLUDED_CATEGORY_IDS),
            help="Comma-separated category ids to skip (default: 1,2).",
        )
        parser.add_argument(
            "--replace-existing",
            action="store_true",
            help="Delete existing Category rows before import (recommended for one-time bootstrap).",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Do not write changes, only print summary.",
        )

    def handle(self, *args, **options):
        source_path = Path(str(options.get("source") or DEFAULT_SOURCE_PATH))
        allowed_lang_ids = _parse_ids(options.get("allowed_lang_ids") or "")
        lang_priority = [
            x.strip()
            for x in str(options.get("lang_priority") or "").split(",")
            if x.strip()
        ]
        excluded_category_ids = _parse_ids(options.get("exclude_category_ids") or "")
        replace_existing = bool(options.get("replace_existing"))
        dry_run = bool(options.get("dry_run"))
        base_language_code = "lt"

        if not source_path.exists():
            raise CommandError(f"Source file not found: {source_path}")

        if not allowed_lang_ids:
            raise CommandError("--allowed-lang-ids is empty.")
        if not lang_priority:
            raise CommandError("--lang-priority is empty.")

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

        grouped: dict[str, dict[str, dict]] = {}
        parent_by_category: dict[str, str] = {}
        depth_by_category: dict[str, int] = {}

        skipped_excluded = 0
        skipped_lang = 0

        for row in rows:
            if not isinstance(row, dict):
                continue
            category_id = str(row.get("id_category", "")).strip()
            lang_id = str(row.get("id_lang", "")).strip()
            parent_id = str(row.get("id_parent", "")).strip()
            depth = _to_int(row.get("level_depth"), default=0)

            if not category_id:
                continue
            if category_id in excluded_category_ids:
                skipped_excluded += 1
                continue
            if lang_id not in allowed_lang_ids:
                skipped_lang += 1
                continue

            grouped.setdefault(category_id, {})[lang_id] = row
            parent_by_category[category_id] = parent_id
            depth_by_category[category_id] = depth

        selected: list[CategorySource] = []
        for category_id, by_lang in grouped.items():
            preferred = None
            for lang_id in lang_priority:
                preferred = by_lang.get(lang_id)
                if preferred is not None:
                    break
            if preferred is None:
                # Fallback deterministic pick if priority list has no match.
                first_lang = sorted(by_lang.keys())[0]
                preferred = by_lang[first_lang]

            selected.append(
                CategorySource(
                    id_category=category_id,
                    id_parent=parent_by_category.get(category_id, ""),
                    level_depth=depth_by_category.get(category_id, 0),
                    base_row=preferred,
                    rows_by_lang=by_lang,
                )
            )

        selected.sort(key=lambda x: (x.level_depth, _to_int(x.id_category, default=10**9)))

        created = 0
        updated = 0
        created_translations = 0
        updated_translations = 0

        if dry_run:
            self.stdout.write(
                self.style.WARNING(
                    f"Dry-run: would import categories={len(selected)} (excluded_rows={skipped_excluded}, skipped_lang_rows={skipped_lang})."
                )
            )
            return

        with transaction.atomic():
            if replace_existing:
                # Category.parent uses PROTECT (self-reference), so bulk delete requires
                # detaching the tree first.
                Category.objects.update(parent=None)
                # Will still fail with ProtectedError if categories are referenced by products.
                Category.objects.all().delete()

            # Keep in-memory mapping from external Presta category id -> created/updated Category.
            internal_by_external_id: dict[str, Category] = {}

            # Track occupied slugs inside this transaction for deterministic uniqueness.
            used_slugs = set(Category.objects.values_list("slug", flat=True))
            used_translation_slugs: dict[str, set[str]] = {}
            for lang_code, slug in CategoryTranslation.objects.values_list("language_code", "slug"):
                used_translation_slugs.setdefault(str(lang_code), set()).add(str(slug))

            for src in selected:
                row = src.base_row
                name = (row.get("name") or "").strip() or f"Category {src.id_category}"
                slug_hint = (row.get("link_rewrite") or "").strip() or slugify(name) or f"category-{src.id_category}"
                is_active = str(row.get("active", "1")).strip() == "1"
                description = _to_markdown_from_html(row.get("description"))

                parent = None
                parent_external_id = src.id_parent
                if parent_external_id and parent_external_id not in excluded_category_ids:
                    parent = internal_by_external_id.get(parent_external_id)

                existing_category = Category.objects.filter(external_presta_id=src.id_category).first()
                if existing_category is None:
                    slug = _unique_slug(
                        slug_hint,
                        existing=used_slugs,
                        stable_suffix=src.id_category,
                    )
                    category = Category.objects.create(
                        external_presta_id=src.id_category,
                        name=name,
                        slug=slug,
                        parent=parent,
                        description=description,
                        is_active=is_active,
                    )
                    was_created = True
                else:
                    # Preserve existing slug to avoid URL churn and uniqueness conflicts.
                    used_slugs.add(existing_category.slug)
                    existing_category.name = name
                    existing_category.parent = parent
                    existing_category.description = description
                    existing_category.is_active = is_active
                    existing_category.save(
                        update_fields=["name", "parent", "description", "is_active"]
                    )
                    category = existing_category
                    was_created = False

                internal_by_external_id[src.id_category] = category
                if was_created:
                    created += 1
                else:
                    updated += 1

                for presta_lang_id, tr_row in src.rows_by_lang.items():
                    language_code = LANG_CODE_BY_PRESTA_LANG_ID.get(presta_lang_id)
                    if not language_code:
                        continue
                    # Base content is LT; keep non-LT locales in translation table.
                    if language_code == base_language_code:
                        continue

                    tr_name = (tr_row.get("name") or "").strip()
                    tr_slug = (
                        (tr_row.get("link_rewrite") or "").strip()
                        or slugify(tr_name)
                        or f"category-{src.id_category}-{language_code}"
                    )
                    tr_slug = _unique_translation_slug(
                        language_code,
                        tr_slug,
                        existing_by_lang=used_translation_slugs,
                        stable_suffix=src.id_category,
                    )

                    translation, tr_created = CategoryTranslation.objects.update_or_create(
                        category=category,
                        language_code=language_code,
                        defaults={
                            "name": tr_name,
                            "slug": tr_slug,
                            "description": _to_markdown_from_html(tr_row.get("description")),
                            "seo_title": (tr_row.get("meta_title") or "").strip(),
                            "seo_description": (tr_row.get("meta_description") or "").strip(),
                            "seo_keywords": (tr_row.get("meta_keywords") or "").strip(),
                        },
                    )
                    if tr_created:
                        created_translations += 1
                    else:
                        updated_translations += 1

        self.stdout.write(
            self.style.SUCCESS(
                "Presta categories import done. "
                f"created={created}, updated={updated}, total={len(selected)}, "
                f"translations_created={created_translations}, translations_updated={updated_translations}, "
                f"excluded_rows={skipped_excluded}, skipped_lang_rows={skipped_lang}."
            )
        )
