from __future__ import annotations

import json
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from catalog_import.models import ImportRun
from catalog_import.services import create_run, execute_run


class Command(BaseCommand):
    help = "Import a normalized catalog JSON file (stage + apply, per-item transactions)."

    def add_arguments(self, parser):
        parser.add_argument("file", type=str, help="Path to normalized catalog JSON")
        parser.add_argument(
            "--source",
            type=str,
            default="",
            help="Override source identifier (default: from file)",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Validate only, roll back all writes",
        )
        parser.add_argument(
            "--no-apply",
            action="store_true",
            help="Only stage the run; apply later via ops API",
        )

    def handle(self, *args, **options):
        path = Path(options["file"])
        if not path.is_file():
            raise CommandError(f"File not found: {path}")
        payload_bytes = path.read_bytes()

        source = options["source"]
        if source:
            try:
                data = json.loads(payload_bytes)
            except json.JSONDecodeError as exc:
                raise CommandError(f"Invalid JSON: {exc}") from exc
            data["source"] = source
            payload_bytes = json.dumps(data).encode("utf-8")

        run = create_run(
            kind=ImportRun.Kind.CATALOG,
            payload_bytes=payload_bytes,
            file_name=path.name,
            options={"dry_run": options["dry_run"]},
        )
        self.stdout.write(
            f"Run #{run.id} staged ({run.file_name}, sha256={run.file_sha256[:12]}...)"
        )
        if options["no_apply"]:
            self.stdout.write("Skipped apply (--no-apply).")
            return

        execute_run(run)
        self.stdout.write(
            f"Done: status={run.status} total={run.total_items} "
            f"ok={run.ok_items} failed={run.failed_items} skipped={run.skipped_items}"
        )
        if run.error_message:
            self.stderr.write(f"error: {run.error_message}")
        for item in run.items.filter(status="error")[:20]:
            self.stderr.write(f"  row {item.row_no} [{item.external_code}]: {item.error}")
