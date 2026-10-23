"""Propose (and optionally write) per-category facet config (F4).

For every active category with products in its subtree, picks the top
varying attribute codes (cap 8) — feature codes and option-type codes share
one canonical namespace because same-code facets merge. Codes whose
Feature/OptionType datatype is 'number' or 'range' are proposed as
'range' widgets; the rest as 'checkbox'.

Default is --dry-run (report only); --apply upserts CategoryFacet rows.
Existing rows for codes not in the proposal are left untouched.
"""

from django.core.management.base import BaseCommand

from catalog.models import (
    AttributeDatatype,
    Category,
    CategoryFacet,
    Feature,
    OptionType,
    ProductFeatureValue,
    VariantFeatureValue,
    VariantOptionValue,
)
from catalog.services.catalog_core import descendant_category_ids

CAP = 8


def _propose_for_category(cat: Category, *, cap: int = CAP) -> list[dict]:
    """Top `cap` attribute codes for products in cat's subtree.

    Returns [{code, source, widget, n_values}] ordered by distinct-value
    count desc (the most discriminating axes first), code asc as tiebreak.
    """
    from catalog.models import Product

    ids = list(
        Product.objects.filter(
            is_active=True,
            category_id__in=descendant_category_ids(root_id=int(cat.id)),
        ).values_list("id", flat=True)
    )
    if not ids:
        return []

    # Distinct values per canonical code + which side (feature/option)
    # the code lives on. Feature and option codes share one namespace —
    # same-code facets merge in the payload.
    val_counts: dict[str, set] = {}
    sides: dict[str, set[str]] = {}
    for qs, code_key, val_key, side in (
        (
            ProductFeatureValue.objects.filter(product_id__in=ids),
            "feature__code",
            "feature_value_id",
            "feature",
        ),
        (
            VariantFeatureValue.objects.filter(variant__product_id__in=ids),
            "feature__code",
            "feature_value_id",
            "feature",
        ),
        (
            VariantOptionValue.objects.filter(variant__product_id__in=ids),
            "option_type__code",
            "option_value_id",
            "option",
        ),
    ):
        for code, val in qs.values_list(code_key, val_key).distinct():
            val_counts.setdefault(code, set()).add(val)
            sides.setdefault(code, set()).add(side)

    numeric = (AttributeDatatype.NUMBER, AttributeDatatype.RANGE)
    feature_dt = dict(
        Feature.objects.filter(code__in=val_counts).values_list("code", "datatype")
    )
    option_dt = dict(
        OptionType.objects.filter(code__in=val_counts).values_list("code", "datatype")
    )

    ranked = sorted(
        val_counts.items(),
        key=lambda kv: (-len(kv[1]), kv[0]),
    )[:cap]

    out = []
    for code, vals in ranked:
        dt = feature_dt.get(code) or option_dt.get(code) or ""
        code_sides = sides.get(code, set())
        if code_sides == {"feature"}:
            source = CategoryFacet.Source.FEATURE
        elif code_sides == {"option"}:
            source = CategoryFacet.Source.OPTION
        else:
            source = CategoryFacet.Source.AUTO
        out.append(
            {
                "code": code,
                "source": source,
                "widget": (
                    CategoryFacet.Widget.RANGE
                    if dt in numeric
                    else CategoryFacet.Widget.CHECKBOX
                ),
                "n_values": len(vals),
            }
        )
    return out


class Command(BaseCommand):
    help = (
        "Propose CategoryFacet rows per category from real data "
        "(dry-run; --apply writes)."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--apply",
            action="store_true",
            help="Write the proposed config (default: dry-run report).",
        )
        parser.add_argument(
            "--category",
            default="",
            help="Restrict to one category slug.",
        )
        parser.add_argument(
            "--cap",
            type=int,
            default=CAP,
            help="Max facets proposed per category (default: 8).",
        )
        parser.add_argument(
            "--roots-only",
            action="store_true",
            help="Seed only root categories — leaves inherit via MPTT "
            "ancestor lookup (recommended with facet inheritance).",
        )

    def handle(self, *args, **options):
        apply_ = bool(options["apply"])
        cap = int(options["cap"] or CAP)
        slug = (options["category"] or "").strip()

        cats = Category.objects.filter(is_active=True).order_by("tree_id", "lft")
        if options.get("roots_only"):
            cats = cats.filter(parent__isnull=True)
        if slug:
            cats = cats.filter(slug=slug)
            if not cats.exists():
                self.stderr.write(self.style.ERROR(f"Category '{slug}' not found"))
                return

        created = updated = 0
        for cat in cats:
            proposal = _propose_for_category(cat, cap=cap)
            if not proposal:
                continue
            self.stdout.write(f"{cat.slug} ({cat.name}):")
            for i, row in enumerate(proposal):
                self.stdout.write(
                    f"  {i + 1}. {row['code']:<28} {row['widget']:<8} "
                    f"{row['source']:<7} {row['n_values']} values"
                )
                if apply_:
                    _, was_created = CategoryFacet.objects.update_or_create(
                        category=cat,
                        code=row["code"],
                        defaults={
                            "source": row["source"],
                            "widget": row["widget"],
                            "order": (i + 1) * 10,
                            "is_enabled": True,
                        },
                    )
                    if was_created:
                        created += 1
                    else:
                        updated += 1

        if apply_:
            self.stdout.write(
                self.style.SUCCESS(
                    f"applied: {created} created, {updated} updated"
                )
            )
        else:
            self.stdout.write(
                "dry-run — no writes. Re-run with --apply to seed "
                "CategoryFacet rows."
            )
