"""Kategorijų hero/kortelių AVIF/WebP rendition'os.

  manage.py regenerate_category_images            # tik pasikeitusios / trūkstamos
  manage.py regenerate_category_images --force --slug zvejybos-iranga
"""

from django.core.management.base import BaseCommand

from catalog.models import Category
from catalog.services.category_images import KINDS, needs_renditions, process_category


class Command(BaseCommand):
    help = "Generuoja kategorijų hero/card AVIF/WebP rendition'as."

    def add_arguments(self, parser):
        parser.add_argument("--slug", action="append", default=[])
        parser.add_argument("--force", action="store_true")
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, **options):
        qs = Category.objects.order_by("tree_id", "lft")
        if options["slug"]:
            qs = qs.filter(slug__in=options["slug"])
        done = 0
        for c in qs:
            kinds = [k for k in KINDS if options["force"] or needs_renditions(c, k)]
            if not kinds:
                continue
            if options["dry_run"]:
                self.stdout.write(f"{c.slug}: {', '.join(kinds)}")
                done += 1
                continue
            changed = process_category(c, force=options["force"])
            errors = [k for k in changed if (c.image_renditions.get(k) or {}).get("error")]
            self.stdout.write(f"{c.slug}: {', '.join(changed)}" + (f" ERROR {errors}" if errors else ""))
            done += 1
        self.stdout.write(self.style.SUCCESS(f"categories: {done}"))
