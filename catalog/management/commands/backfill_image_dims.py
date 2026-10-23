"""Uzpildo ProductImage.width/height esamiems paveikslams.

Begerendoruoja rendition'u — atidaro tik header'i (Image.open nedecod'uoja
pixels). Naudoti po width/height lauku idegimo:

  manage.py backfill_image_dims --workers 8
"""

from concurrent.futures import ThreadPoolExecutor

from django.core.management.base import BaseCommand
from django.db import connection

from catalog.models import ProductImage


class Command(BaseCommand):
    help = "Backfill'ina ProductImage.width/height is original failu."

    def add_arguments(self, parser):
        parser.add_argument("--workers", type=int, default=8)
        parser.add_argument("--limit", type=int, default=0)

    def handle(self, *args, **options):
        ids = list(
            ProductImage.objects.exclude(image="")
            .filter(width__isnull=True)
            .order_by("id")
            .values_list("id", flat=True)[: options["limit"] or None]
        )
        self.stdout.write(f"images missing dims: {len(ids)}")
        if not ids:
            return

        workers = max(1, int(options["workers"] or 8))

        def work(pk):
            try:
                from PIL import Image
                import pillow_avif  # noqa: F401

                obj = ProductImage.objects.only("id", "image").get(pk=pk)
                if not obj.image:
                    return
                obj.image.open()
                with Image.open(obj.image) as im:
                    w, h = im.size
                ProductImage.objects.filter(pk=pk).update(width=w, height=h)
            except Exception:
                pass
            finally:
                connection.close()

        with ThreadPoolExecutor(max_workers=workers) as pool:
            for i, _ in enumerate(pool.map(work, ids), 1):
                if i % 200 == 0:
                    self.stdout.write(f"  {i}/{len(ids)}")
        self.stdout.write(self.style.SUCCESS(f"finished {len(ids)}"))
