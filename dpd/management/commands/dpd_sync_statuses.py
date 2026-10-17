from django.core.management.base import BaseCommand

from checkout.models import Order, Shipment
from dpd.client import DpdApiError, DpdClient
from dpd.status_mapping import extract_latest_event, map_dpd_status

TERMINAL = {
    Shipment.Status.DELIVERED,
    Shipment.Status.RETURNED,
    Shipment.Status.CANCELLED,
}


class Command(BaseCommand):
    help = "Sync DPD delivery statuses for shipments (orders) with tracking_number"

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=200)
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, **options):
        limit: int = int(options["limit"])
        dry_run: bool = bool(options["dry_run"])

        qs = (
            Shipment.objects.filter(carrier_code="dpd")
            .exclude(tracking_number="")
            .exclude(status__in=TERMINAL)
            .select_related("order")
            .order_by("-updated_at")
        )

        processed = 0
        updated = 0
        failed = 0

        client = DpdClient()

        for shipment in qs[:limit]:
            processed += 1
            tracking = (shipment.tracking_number or "").strip()
            try:
                raw = client.get_status(
                    pknr=tracking, detail="0", show_all="0")
            except DpdApiError as e:
                failed += 1
                self.stderr.write(
                    f"shipment={shipment.id} order={shipment.order_id} "
                    f"tracking={tracking}: {e}")
                continue

            if not raw:
                continue

            event = extract_latest_event(raw)
            new_status = map_dpd_status(
                status_code=event["status_code"],
                service_codes=event["service_codes"],
                prev_status_code=event["prev_status_code"],
            )
            if new_status is None or new_status == shipment.status:
                continue

            if not dry_run:
                shipment.status = new_status
                shipment.save(update_fields=["status", "updated_at"])
                # delivered_at is anchored by the Order signal on the
                # transition to DELIVERED; aggregate keeps multi-parcel
                # orders accurate.
                if shipment.kind == Shipment.Kind.OUTBOUND:
                    shipment.apply_to_order_projection()
            updated += 1

        self.stdout.write(
            f"Done. processed={processed} updated={updated} failed={failed} dry_run={dry_run}"
        )
