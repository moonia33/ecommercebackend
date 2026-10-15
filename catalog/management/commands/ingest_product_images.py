from __future__ import annotations

from django.core.management.base import BaseCommand

from catalog.models import ProductImage


class Command(BaseCommand):
    help = (
        "Download external ProductImage.image_url into object storage "
        "(image field) and queue rendition generation. Runs ingest task "
        "per image — Celery async by default, --sync runs inline."
    )

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=None)
        parser.add_argument("--sync", action="store_true", default=False)
        parser.add_argument(
            "--retry-errors",
            action="store_true",
            default=False,
            help="also retry images currently in error state",
        )

    def handle(self, *args, **options):
        from catalog.tasks import ingest_product_image_task
        from config.celery import safe_delay

        qs = ProductImage.objects.filter(image_url__gt="").filter(
            image__isnull=True
        )
        if options["retry_errors"]:
            qs = qs.exclude(ingest_status=ProductImage.INGEST_DONE)
        else:
            qs = qs.exclude(
                ingest_status__in=[
                    ProductImage.INGEST_DONE, ProductImage.INGEST_ERROR]
            )

        qs = qs.order_by("id")
        if options["limit"] is not None:
            qs = qs[: max(0, int(options["limit"]))]

        queued = 0
        for obj in qs.iterator():
            if not obj.ingest_status == ProductImage.INGEST_PENDING:
                obj.ingest_status = ProductImage.INGEST_PENDING
                obj.ingest_error = ""
                obj.save(update_fields=["ingest_status", "ingest_error"])
            if options["sync"]:
                ingest_product_image_task(obj.pk)
            else:
                safe_delay(ingest_product_image_task, obj.pk)
            queued += 1

        self.stdout.write(self.style.SUCCESS(f"Queued {queued} image ingest task(s)"))
