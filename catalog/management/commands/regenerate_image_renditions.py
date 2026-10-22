"""Regeneruoja ProductImage rendition'as (m300/d800/sq300/sq600).

Naudojimas po nauju rendition lauku idegimo ar dydziu pasikeitimo:
  manage.py regenerate_image_renditions --only-missing --workers 8
"""

from concurrent.futures import ThreadPoolExecutor

from django.core.management.base import BaseCommand
from django.db import connection

from catalog.models import ProductImage


class Command(BaseCommand):
    help = "Regeneruoja image rendition'as (--only-missing, --workers N)."

    def add_arguments(self, parser):
        parser.add_argument("--workers", type=int, default=8)
        parser.add_argument("--limit", type=int, default=0)
        parser.add_argument("--only-missing", action="store_true", default=False)

    def handle(self, *args, **options):
        from catalog.tasks import process_product_image_task

        from django.db.models import Q

        qs = ProductImage.objects.exclude(image="").order_by("id")
        if options["only_missing"]:
            qs = qs.filter(Q(detail_avif__isnull=True) | Q(detail_avif=""))
        ids = list(qs.values_list("id", flat=True)[: options["limit"] or None])
        self.stdout.write(f"images: {len(ids)}")
        if not ids:
            return

        workers = max(1, int(options["workers"] or 8))

        def work(pk):
            try:
                process_product_image_task.apply(args=[pk])
            finally:
                connection.close()

        with ThreadPoolExecutor(max_workers=workers) as pool:
            for i, _ in enumerate(pool.map(work, ids), 1):
                if i % 200 == 0:
                    self.stdout.write(f"  {i}/{len(ids)}")
        self.stdout.write(self.style.SUCCESS(f"finished {len(ids)}"))
