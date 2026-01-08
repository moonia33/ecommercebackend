from __future__ import annotations

from decimal import Decimal

from django.core.management.base import BaseCommand

from unisend.client import UnisendApiError, UnisendClient
from unisend.models import UnisendTerminal


class Command(BaseCommand):
    help = "Sync Unisend/LPExpress terminals into local DB for checkout validation and admin search."

    def add_arguments(self, parser):
        parser.add_argument("--country-code", default="LT")
        parser.add_argument("--limit", type=int, default=10000)
        parser.add_argument("--deactivate-missing", action="store_true")

    def handle(self, *args, **opts):
        country_code = str(opts["country_code"] or "LT").strip().upper()
        limit = int(opts["limit"] or 10000)
        deactivate_missing = bool(opts["deactivate_missing"])

        try:
            raw = UnisendClient().list_terminals(receiver_country_code=country_code, size=limit)
        except UnisendApiError as e:
            raise SystemExit(str(e))

        items = raw
        if isinstance(raw, dict) and isinstance(raw.get("items"), list):
            items = raw.get("items")

        if not isinstance(items, list):
            raise SystemExit("Unisend terminals sync: unexpected response")

        seen_ids: set[str] = set()
        created = 0
        updated = 0

        for it in items:
            if not isinstance(it, dict):
                continue

            tid = str(it.get("terminalId") or it.get("id") or "").strip()
            if not tid:
                continue
            seen_ids.add(tid)

            obj, was_created = UnisendTerminal.objects.get_or_create(terminal_id=tid)
            obj.country_code = str(it.get("countryCode") or country_code or "").strip().upper()
            obj.name = str(it.get("name") or "").strip()
            obj.locality = str(it.get("locality") or it.get("city") or "").strip()
            obj.street = str(
                it.get("street")
                or it.get("address")
                or ""
            ).strip()
            obj.postal_code = str(it.get("postalCode") or "").strip()

            lat = it.get("latitude")
            lng = it.get("longitude")
            try:
                obj.latitude = Decimal(str(lat)) if lat is not None and str(lat).strip() else None
            except Exception:
                obj.latitude = None
            try:
                obj.longitude = Decimal(str(lng)) if lng is not None and str(lng).strip() else None
            except Exception:
                obj.longitude = None

            obj.raw = it
            obj.is_active = True
            obj.save()

            if was_created:
                created += 1
            else:
                updated += 1

        if deactivate_missing:
            UnisendTerminal.objects.filter(country_code=country_code).exclude(
                terminal_id__in=seen_ids
            ).update(is_active=False)

        self.stdout.write(
            self.style.SUCCESS(
                f"Unisend terminals synced: created={created}, updated={updated}, total_seen={len(seen_ids)}"
            )
        )
