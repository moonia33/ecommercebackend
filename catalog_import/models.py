from __future__ import annotations

from django.conf import settings
from django.db import models


class ImportRun(models.Model):
    class Kind(models.TextChoices):
        CATALOG = "catalog", "Catalog"
        STOCK = "stock", "Stock"

    class Status(models.TextChoices):
        QUEUED = "queued", "Queued"
        PARSING = "parsing", "Parsing"
        APPLYING = "applying", "Applying"
        DONE = "done", "Done"
        DONE_WITH_ERRORS = "done_with_errors", "Done with errors"
        FAILED = "failed", "Failed"

    kind = models.CharField(max_length=20, choices=Kind.choices)
    source = models.CharField(
        max_length=64,
        blank=True,
        default="",
        help_text="Import source identifier from the payload (e.g. 'normalizer').",
    )
    file_name = models.CharField(max_length=255, blank=True, default="")
    file_sha256 = models.CharField(max_length=64, blank=True, default="", db_index=True)
    payload = models.TextField(
        blank=True,
        default="",
        help_text="Raw JSON payload (staged for apply/review).",
    )
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.QUEUED, db_index=True
    )
    class Mode(models.TextChoices):
        UPSERT = "upsert", "Upsert"
        FULL_SYNC = "full_sync", "Full sync"

    mode = models.CharField(
        max_length=20,
        choices=Mode.choices,
        default=Mode.UPSERT,
        help_text="upsert | full_sync",
    )
    options = models.JSONField(
        default=dict,
        blank=True,
        help_text="Import flags: dry_run, create_missing_taxonomy, warehouse_code, ...",
    )

    total_items = models.PositiveIntegerField(default=0)
    ok_items = models.PositiveIntegerField(default=0)
    failed_items = models.PositiveIntegerField(default=0)
    skipped_items = models.PositiveIntegerField(default=0)

    error_message = models.TextField(blank=True, default="")

    triggered_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="import_runs",
    )

    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [
            models.Index(fields=["kind", "status"]),
            models.Index(fields=["source", "file_sha256"]),
        ]

    def recount(self) -> None:
        agg = self.items.aggregate(
            total=models.Count("id"),
            ok=models.Count("id", filter=models.Q(status=ImportItem.Status.OK)),
            failed=models.Count("id", filter=models.Q(status=ImportItem.Status.ERROR)),
            skipped=models.Count(
                "id", filter=models.Q(status=ImportItem.Status.SKIPPED)
            ),
        )
        self.total_items = agg["total"]
        self.ok_items = agg["ok"]
        self.failed_items = agg["failed"]
        self.skipped_items = agg["skipped"]

    def __str__(self) -> str:
        return f"{self.kind}:{self.id} {self.status}"


class ImportItem(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        OK = "ok", "OK"
        ERROR = "error", "Error"
        SKIPPED = "skipped", "Skipped"

    run = models.ForeignKey(
        ImportRun, on_delete=models.CASCADE, related_name="items"
    )
    row_no = models.PositiveIntegerField()
    external_code = models.CharField(
        max_length=128,
        blank=True,
        default="",
        db_index=True,
        help_text="Item code/identifier from the payload (for traceability).",
    )
    payload = models.JSONField(default=dict, blank=True)
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.PENDING, db_index=True
    )
    error = models.TextField(blank=True, default="")
    product = models.ForeignKey(
        "catalog.Product",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="import_items",
    )
    variant = models.ForeignKey(
        "catalog.Variant",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="import_items",
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["row_no", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["run", "row_no"],
                name="uniq_import_item_run_row",
            ),
        ]
        indexes = [
            models.Index(fields=["run", "status"]),
        ]

    def __str__(self) -> str:
        return f"{self.run_id}:{self.row_no} {self.status}"
