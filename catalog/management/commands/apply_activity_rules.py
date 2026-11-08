from __future__ import annotations

from django.core.management.base import BaseCommand

from catalog.activities import apply_activity_rules
from catalog.models import ActivityRule


class Command(BaseCommand):
    help = "Apply ActivityRule rules: add matching ProductActivity rows, remove stale ones."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true", default=False)
        parser.add_argument("--rule-id", action="append", type=int, default=None)
        parser.add_argument("--verbose-rules", action="store_true", default=False)

    def handle(self, *args, **opts):
        result = apply_activity_rules(rule_ids=opts["rule_id"], dry_run=opts["dry_run"])
        if opts["verbose_rules"]:
            names = dict(ActivityRule.objects.values_list("id", "name"))
            codes = dict(ActivityRule.objects.values_list("id", "activity__code"))
            for rid, n in sorted(result.matched.items()):
                self.stdout.write(f"  #{rid} {codes.get(rid)}: {names.get(rid)} → {n}")
        self.stdout.write(
            f"rules={len(result.matched)} matched={sum(result.matched.values())} "
            f"added={result.added} removed={result.removed}"
            + (" (dry-run)" if opts["dry_run"] else "")
        )
