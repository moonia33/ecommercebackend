from __future__ import annotations

from datetime import timedelta

from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone

from checkout.services import expire_pending_payment_reservations


class Command(BaseCommand):
    help = "Expire inventory reservations for pending_payment orders (release reserved qty and cancel orders)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Only calculate and print how many would be expired; do not change DB.",
        )

    def handle(self, *args, **options):
        dry_run: bool = bool(options.get("dry_run"))
        if dry_run:
            from checkout.models import Order, PaymentIntent

            now = timezone.now()
            ttl_gateway_min = int(getattr(settings, "INVENTORY_RESERVATION_TTL_MINUTES_GATEWAY", 30) or 30)
            ttl_bank_hours = int(getattr(settings, "INVENTORY_RESERVATION_TTL_HOURS_BANK_TRANSFER", 72) or 72)
            cutoff_gateway = now - timedelta(minutes=ttl_gateway_min)
            cutoff_bank = now - timedelta(hours=ttl_bank_hours)

            qs = (
                Order.objects.filter(status=Order.Status.PENDING_PAYMENT)
                .select_related("payment_intent")
                .order_by("id")
            )

            would_expire = 0
            for o in qs:
                pi = getattr(o, "payment_intent", None)
                provider = (getattr(pi, "provider", "") or "").strip()
                if provider == PaymentIntent.Provider.BANK_TRANSFER:
                    if o.created_at < cutoff_bank:
                        would_expire += 1
                else:
                    if o.created_at < cutoff_gateway:
                        would_expire += 1

            self.stdout.write(self.style.WARNING(f"dry-run: would expire pending orders: {would_expire}"))
            return

        expired = expire_pending_payment_reservations()
        self.stdout.write(self.style.SUCCESS(f"Expired pending orders: {expired}"))
