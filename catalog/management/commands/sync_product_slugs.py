from django.core.management.base import BaseCommand
from django.db import transaction

from catalog.models import Product, ProductTranslation
from catalog.services.slugs import assign_product_slug, assign_translation_slug


class Command(BaseCommand):
    help = (
        "Perrašo prekių slug'us pagal kiekvienos kalbos pavadinimą "
        "(Product.slug – LT, ProductTranslation.slug – kitos kalbos). "
        "Seni slug'ai išsaugomi ProductSlugRedirect (301)."
    )

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true")
        parser.add_argument("--batch-size", type=int, default=500)

    def handle(self, *args, dry_run=False, batch_size=500, **options):
        changed_products = changed_translations = 0
        examples: list[str] = []

        ids = list(Product.objects.order_by("id").values_list("id", flat=True))
        for start in range(0, len(ids), batch_size):
            chunk = ids[start:start + batch_size]
            with transaction.atomic():
                for product in Product.objects.filter(id__in=chunk).order_by("id"):
                    old = product.slug
                    if not product.name.strip():
                        continue
                    if dry_run:
                        from catalog.services.slugs import slug_base, slug_matches

                        if slug_matches(old, slug_base(product.name)):
                            continue
                        changed_products += 1
                        if len(examples) < 10:
                            examples.append(f"lt: {old} -> {slug_base(product.name)}")
                        continue
                    if assign_product_slug(product, product.name):
                        product.save(update_fields=["slug"])
                        changed_products += 1
                        if len(examples) < 10:
                            examples.append(f"lt: {old} -> {product.slug}")

                trs = ProductTranslation.objects.filter(product_id__in=chunk).order_by("id")
                for tr in trs:
                    old = tr.slug
                    if not tr.name.strip():
                        continue
                    if dry_run:
                        from catalog.services.slugs import slug_base, slug_matches

                        if not slug_matches(old, slug_base(tr.name)):
                            changed_translations += 1
                        continue
                    if assign_translation_slug(tr, tr.name):
                        tr.save(update_fields=["slug"])
                        changed_translations += 1
            self.stdout.write(f"{min(start + batch_size, len(ids))}/{len(ids)}")

        for line in examples:
            self.stdout.write(line)
        self.stdout.write(
            f"{'[dry-run] ' if dry_run else ''}products: {changed_products}, "
            f"translations: {changed_translations}"
        )
