"""Download pending ProductImage.image_url files into object storage.

Import runs suppress per-row image ingest (`_suppress_image_ingest`) so a
catalog sync doesn't block on upstream HTTP. This command drains the
INGEST_PENDING backlog — run it after imports (cron/celery-beat/manual).
"""

from concurrent.futures import ThreadPoolExecutor

from django.core.management.base import BaseCommand
from django.db import connection

from catalog.models import ProductImage


class Command(BaseCommand):
    help = "Download pending product images (--workers N, default 8)."

    def add_arguments(self, parser):
        parser.add_argument("--workers", type=int, default=8)
        parser.add_argument("--limit", type=int, default=0)
        parser.add_argument(
            "--retry-errors",
            action="store_true",
            default=False,
            help="Taip pat apdoroti INGEST_ERROR irasus (transient/ispaistos klaidos).",
        )

    def handle(self, *args, **options):
        from catalog.tasks import ingest_product_image_task

        statuses = [ProductImage.INGEST_PENDING]
        if options["retry_errors"]:
            statuses.append(ProductImage.INGEST_ERROR)
        qs = (
            ProductImage.objects.filter(
                ingest_status__in=statuses, image=""
            )
            .exclude(image_url="")
            .order_by("id")
            .values_list("id", flat=True)
        )
        limit = int(options["limit"] or 0)
        ids = list(qs[: limit or None])
        self.stdout.write(f"pending: {len(ids)}")
        if not ids:
            return

        workers = max(1, int(options["workers"] or 8))
        done = err = 0

        def work(pk):
            try:
                ingest_product_image_task.apply(args=[pk])
            finally:
                connection.close()

        with ThreadPoolExecutor(max_workers=workers) as pool:
            for i, _ in enumerate(pool.map(work, ids), 1):
                if i % 500 == 0:
                    ok = ProductImage.objects.filter(
                        id__in=ids[:i], ingest_status=ProductImage.INGEST_DONE
                    ).count()
                    self.stdout.write(f"  {i}/{len(ids)} done={ok}")
        done = ProductImage.objects.filter(
            id__in=ids, ingest_status=ProductImage.INGEST_DONE
        ).count()
        err = ProductImage.objects.filter(
            id__in=ids, ingest_status=ProductImage.INGEST_ERROR
        ).count()
        self.stdout.write(f"finished: done={done} errors={err} of {len(ids)}")
