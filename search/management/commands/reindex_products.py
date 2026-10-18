from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Rebuild the Typesense products collection from the database"

    def handle(self, *args, **options):
        from search.services import full_reindex

        result = full_reindex()
        self.stdout.write(self.style.SUCCESS(str(result)))
