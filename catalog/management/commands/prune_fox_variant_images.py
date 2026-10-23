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

from django.core.management.base import BaseCommand
from django.db.models import Count

from catalog.models import Product, ProductImage
from catalog.services.image_cleanup import bulk_delete_product_images


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

        deleted = bulk_delete_product_images(to_delete, workers=workers)
        self.stdout.write(
            self.style.SUCCESS(f"rows deleted={deleted}")
        )
