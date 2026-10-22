"""Pasalina Fox variantu nuotrauku dublikatus.

Fox feed'as kiekvienam offer'ui (=variantui) duoda PILNA produkto galerija
(Main + InUse/Studio roles) su skirtingais asset id — pozicijos 1+ yra tos
pacios nuotraukos. Normalyzeris dabar variante emit'uoja tik Main; si komanda
isvalo senus ProductImage irasus:

  - palieka kiekvieno varianto Main (sort_order=0);
  - palieka VIENO varianto tail'a (sort_order>0) kaip bendra galerija
    (prioritetas kanoniniam `FX.<product.sku>` variantui, kitu atveju
    variantui su daugiausia susietu nuotrauku);
  - liekanas trina (failus is saugyklos isvalo post_delete signalas).

Dry-run pagal nutylejima; --apply trina.
"""

from django.core.management.base import BaseCommand
from django.db.models import Count

from catalog.models import Product, ProductImage


class Command(BaseCommand):
    help = "Trina Fox variantu image dublius (--apply)."

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true", default=False)

    def handle(self, *args, **options):
        apply = bool(options["apply"])
        products = Product.objects.filter(
            variants__sku__startswith="FX."
        ).distinct()
        to_delete = []
        to_keep = 0
        for p in products.iterator(chunk_size=500):
            rows = list(
                ProductImage.objects.filter(product=p, variants__isnull=False)
                .distinct()
                .prefetch_related("variants")
            )
            if not rows:
                continue
            mains = [r for r in rows if r.sort_order == 0]
            tails = [r for r in rows if r.sort_order != 0]
            keep = set(r.id for r in mains)
            # vieno varianto tail'as = bendra galerija
            canon = p.variants.filter(sku=f"FX.{p.sku}").first()
            if canon is None:
                counts = (
                    p.variants.annotate(n=Count("variant_images")).order_by("-n")
                )
                canon = counts.first()
            if canon is not None:
                for r in tails:
                    if r.variants.filter(pk=canon.pk).exists():
                        keep.add(r.id)
            to_delete.extend(r for r in rows if r.id not in keep)
            to_keep += len(keep)
        self.stdout.write(
            f"products={products.count()} keep={to_keep} delete={len(to_delete)}"
        )
        if not apply:
            self.stdout.write("dry-run; paleisk su --apply")
            return
        deleted = 0
        for r in to_delete:
            r.delete()
            deleted += 1
        self.stdout.write(self.style.SUCCESS(f"deleted={deleted}"))
