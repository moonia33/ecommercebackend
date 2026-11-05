from __future__ import annotations

from django.core.management.base import BaseCommand

from catalog.badges import apply_badge_rules


class Command(BaseCommand):
    help = "Apply BadgeRule rules: add matching ProductBadge rows, remove stale ones."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true", default=False)
        parser.add_argument("--rule-id", action="append", type=int, default=None)

    def handle(self, *args, **opts):
        result = apply_badge_rules(rule_ids=opts["rule_id"], dry_run=opts["dry_run"])
        self.stdout.write(
            f"rules={len(result.matched)} matched={sum(result.matched.values())} "
            f"added={result.added} removed={result.removed}"
            + (" (dry-run)" if opts["dry_run"] else "")
        )
