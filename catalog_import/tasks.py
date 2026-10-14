from __future__ import annotations

from celery import shared_task

from .models import ImportRun
from .services import execute_run


@shared_task(bind=True, max_retries=0)
def apply_import_run_task(self, run_id: int) -> dict:
    run = ImportRun.objects.filter(id=run_id).first()
    if run is None:
        return {"ok": False, "error": f"run {run_id} not found"}
    if run.status != ImportRun.Status.QUEUED:
        return {"ok": False, "error": f"run {run_id} already {run.status}"}
    execute_run(run)
    return {
        "ok": run.failed_items == 0,
        "run_id": run.id,
        "status": run.status,
        "ok_items": run.ok_items,
        "failed_items": run.failed_items,
    }
