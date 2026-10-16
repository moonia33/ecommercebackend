from django.core.management.base import BaseCommand
from django.utils import timezone

from checkout.models import Order
from dpd.client import DpdApiError, DpdClient
from dpd.status_mapping import extract_latest_event, map_dpd_status


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
            .exclude(
                delivery_status__in=[
                    Order.DeliveryStatus.DELIVERED,
                    Order.DeliveryStatus.RETURNED,
                    Order.DeliveryStatus.CANCELLED,
                ]
            )
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

            if not raw:
                continue

            event = extract_latest_event(raw)
            new_status = map_dpd_status(
                status_code=event["status_code"],
                service_codes=event["service_codes"],
                prev_status_code=event["prev_status_code"],
            )
            if new_status is None or new_status == order.delivery_status:
                continue

            fields = {"delivery_status": new_status}
            if (
                new_status
                in {
                    Order.DeliveryStatus.DELIVERED,
                    Order.DeliveryStatus.AT_PICKUP_POINT,
                }
                and order.delivered_at is None
            ):
                fields["delivered_at"] = timezone.now()

            if not dry_run:
                for k, v in fields.items():
                    setattr(order, k, v)
                order.save(update_fields=list(fields) + ["updated_at"])
            updated += 1

        self.stdout.write(
            f"Done. processed={processed} updated={updated} failed={failed} dry_run={dry_run}"
        )
