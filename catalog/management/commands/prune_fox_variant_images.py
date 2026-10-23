"""Pasalina Fox variantu nuotrauku dublikatus.

Fox feed'as kiekvienam offer'ui (=variantui) duoda PILNA produkto galerija
(Main + InUse/Studio roles) su skirtingais asset id — pozicijos 1+ yra tos
pacios nuotraukos. Normalyzeris dabar variante emit'uoja tik Main; si komanda
isvalo senus ProductImage irasus:

  - palieka kiekvieno varianto Main (sort_order=0);
  - palieka VIENO varianto tail'a (sort_order>0) kaip bendra galerija
    (prioritetas kanoniniam `FX.<product.sku>` variantui, kitu atveju
    variantui su daugiausia susietu nuotrauku);
  - liekanas trina.

Dry-run pagal nutylejima; --apply trina. Trynimas bulk'inis (post_delete
signalas atjungiamas), o failai R2 trinami delete_objects batch'ais —
per-eilutes storage.delete() butu ~HTTP kvietimas per faila.
"""

from concurrent.futures import ThreadPoolExecutor

from django.core.management.base import BaseCommand
from django.db.models import Count
from django.db.models.signals import post_delete

from catalog.models import Product, ProductImage
from catalog.signals import product_image_post_delete

FIELDS = ("image", "image_avif", "image_webp", "listing_avif", "listing_webp")


class Command(BaseCommand):
    help = "Trina Fox variantu image dublius (--apply)."

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true", default=False)
        parser.add_argument("--workers", type=int, default=8)

    def handle(self, *args, **options):
        apply = bool(options["apply"])
        workers = max(1, int(options["workers"] or 8))
        products = Product.objects.filter(
            variants__sku__startswith="FX."
        ).distinct()
        to_delete: list[int] = []
        to_keep = 0
        for p in products.iterator(chunk_size=500):
            rows = list(
                ProductImage.objects.filter(product=p, variants__isnull=False)
                .distinct()
                .prefetch_related("variants")
            )
            if not rows:
                continue
            keep = {r.id for r in rows if r.sort_order == 0}
            canon = p.variants.filter(sku=f"FX.{p.sku}").first()
            if canon is None:
                canon = (
                    p.variants.annotate(n=Count("variant_images"))
                    .order_by("-n", "id")
                    .first()
                )
            if canon is not None:
                for r in rows:
                    if r.sort_order != 0 and r.variants.filter(pk=canon.pk).exists():
                        keep.add(r.id)
            to_delete.extend(r.id for r in rows if r.id not in keep)
            to_keep += len(keep)
        self.stdout.write(
            f"products={products.count()} keep={to_keep} delete={len(to_delete)}"
        )
        if not apply:
            self.stdout.write("dry-run; paleisk su --apply")
            return

        # Failu vardai pries trynima (R2 objektai trinami atskirai)
        names = []
        for r in ProductImage.objects.filter(id__in=to_delete).iterator(
            chunk_size=2000
        ):
            for f in FIELDS:
                v = getattr(r, f, None)
                if v and getattr(v, "name", ""):
                    names.append(v.name)

        post_delete.disconnect(product_image_post_delete, sender=ProductImage)
        try:
            deleted = 0
            for i in range(0, len(to_delete), 5000):
                chunk = to_delete[i : i + 5000]
                deleted += ProductImage.objects.filter(id__in=chunk).delete()[0]
        finally:
            post_delete.connect(
                product_image_post_delete, sender=ProductImage
            )
        self.stdout.write(f"rows deleted={deleted}")

        # R2 delete_objects batch'ais po 1000
        from django.core.files.storage import default_storage

        client = default_storage.connection.meta.client
        bucket = default_storage.bucket_name

        def delete_batch(batch):
            client.delete_objects(
                Bucket=bucket,
                Delete={"Objects": [{"Key": k} for k in batch], "Quiet": True},
            )

        batches = [names[i : i + 1000] for i in range(0, len(names), 1000)]
        done = 0
        with ThreadPoolExecutor(max_workers=workers) as pool:
            for _ in pool.map(delete_batch, batches):
                done += 1
                if done % 20 == 0:
                    self.stdout.write(f"  files {done*1000}/{len(names)}")
        self.stdout.write(
            self.style.SUCCESS(f"files deleted={len(names)}")
        )
