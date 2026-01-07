from __future__ import annotations

from django.core.management.base import BaseCommand

from catalog.models import ProductImage


class Command(BaseCommand):
    help = "Regenerate square (1:1) listing renditions for ProductImage records."

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=None)
        parser.add_argument(
            "--only-missing", action="store_true", default=False)

    def handle(self, *args, **options):
        limit = options.get("limit")
        only_missing = bool(options.get("only_missing"))

        qs = ProductImage.objects.all().order_by("id")
        if only_missing:
            qs = qs.filter(listing_avif__isnull=True,
                           listing_webp__isnull=True)

        if limit is not None:
            qs = qs[: max(0, int(limit))]

        processed = 0
        updated = 0
        skipped = 0

        for img in qs.iterator(chunk_size=200):
            processed += 1
            if not img.image:
                skipped += 1
                continue

            before = (bool(img.listing_avif), bool(img.listing_webp))
            # Trigger save() image processing; we want derived renditions.
            img.save()
            after = (bool(img.listing_avif), bool(img.listing_webp))
            if after != before:
                updated += 1

        self.stdout.write(
            self.style.SUCCESS(
                f"Done. processed={processed}, updated={updated}, skipped={skipped}"
            )
        )
