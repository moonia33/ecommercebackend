from __future__ import annotations

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.utils.dateparse import parse_datetime

from catalog.enrichment import apply_enrichment_rules


User = get_user_model()


class Command(BaseCommand):
    help = "Apply catalog enrichment rules (feature value assignment) to products."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            default=False,
            help="Do not write ProductFeatureValue assignments. Still records EnrichmentRun/EnrichmentMatch audit.",
        )
        parser.add_argument(
            "--rule-id",
            action="append",
            default=None,
            help="Limit execution to a specific rule id. Can be repeated.",
        )
        parser.add_argument(
            "--since",
            type=str,
            default=None,
            help="Only process products updated since this datetime (ISO-8601).",
        )
        parser.add_argument(
            "--limit",
            type=int,
            default=None,
            help="Limit number of products to process.",
        )
        parser.add_argument(
            "--user-id",
            type=int,
            default=None,
            help="Set EnrichmentRun.triggered_by to this user id.",
        )

    def handle(self, *args, **options):
        dry_run = bool(options.get("dry_run"))
        rule_ids = options.get("rule_id")
        since_raw = options.get("since")
        limit = options.get("limit")
        user_id = options.get("user_id")

        since = None
        if since_raw:
            since = parse_datetime(str(since_raw))
            if since is None:
                raise CommandError("Invalid --since value. Expected ISO-8601 datetime.")

        triggered_by = None
        if user_id is not None:
            triggered_by = User.objects.filter(id=int(user_id)).first()
            if triggered_by is None:
                raise CommandError("Invalid --user-id: user not found.")

        run, result = apply_enrichment_rules(
            dry_run=dry_run,
            rule_ids=[int(i) for i in rule_ids] if rule_ids else None,
            since=since,
            limit=limit,
            triggered_by=triggered_by,
        )

        self.stdout.write(
            self.style.SUCCESS(
                "Done. "
                f"run_id={run.id}, "
                f"dry_run={run.dry_run}, "
                f"status={run.status}, "
                f"processed_products={result.processed_products}, "
                f"matched={result.matched}, "
                f"assigned={result.assigned}, "
                f"created_feature_values={result.created_feature_values}, "
                f"skipped_existing={result.skipped_existing}, "
                f"skipped_conflict={result.skipped_conflict}"
            )
        )
