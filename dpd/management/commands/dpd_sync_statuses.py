from __future__ import annotations

from django.core.management.base import BaseCommand

from checkout.models import Order
from dpd.client import DpdApiError, DpdClient


class Command(BaseCommand):
    help = "Sync DPD delivery statuses for orders with tracking_number"

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=200)
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, **options):
        limit: int = int(options["limit"])
        dry_run: bool = bool(options["dry_run"])

        qs = (
            Order.objects.filter(carrier_code="dpd")
            .exclude(tracking_number="")
            .order_by("-updated_at")
        )

        processed = 0
        updated = 0
        failed = 0

        client = DpdClient()

        for order in qs[:limit]:
            processed += 1
            tracking = (order.tracking_number or "").strip()
            try:
                raw = client.get_status(
                    pknr=tracking, detail="0", show_all="0")
            except DpdApiError as e:
                failed += 1
                self.stderr.write(f"order={order.id} tracking={tracking}: {e}")
                continue

            # MVP mapping: we keep it conservative.
            # If DPD returns no statuses, do nothing.
            if not raw:
                continue

            # Try to detect delivered-ish states from payload (best-effort).
            # Different detail levels may vary; for now check common keys.
            latest = raw[0] if isinstance(raw[0], dict) else {}
            status_text = str(
                latest.get("status")
                or latest.get("statusText")
                or latest.get("statusDescription")
                or latest.get("statusName")
                or ""
            ).lower()
            status_code = str(latest.get("statusCode")
                              or latest.get("code") or "").lower()

            new_delivery_status = order.delivery_status
            if any(k in status_text for k in ["delivered", "pristatyta", "atsiimta", "atiduota"]):
                new_delivery_status = Order.DeliveryStatus.DELIVERED
            elif any(k in status_text for k in ["shipped", "on the way", "kelyje", "išsiųsta", "issiusta"]):
                new_delivery_status = Order.DeliveryStatus.SHIPPED
            elif status_code in {"label_created", "created"}:
                new_delivery_status = Order.DeliveryStatus.LABEL_CREATED

            # Store raw last status snapshot into pickup_point_raw for now? No.
            # Keep minimal: just update delivery_status.
            if new_delivery_status != order.delivery_status:
                if not dry_run:
                    order.delivery_status = new_delivery_status
                    order.save(update_fields=["delivery_status", "updated_at"])
                updated += 1

        self.stdout.write(
            f"Done. processed={processed} updated={updated} failed={failed} dry_run={dry_run}"
        )
