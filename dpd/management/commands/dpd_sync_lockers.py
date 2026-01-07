from __future__ import annotations

from decimal import Decimal

from django.core.management.base import BaseCommand

from dpd.client import DpdApiError, DpdClient
from dpd.models import DpdLocker


class Command(BaseCommand):
    help = "Sync DPD lockers into local DB for admin autocomplete."

    def add_arguments(self, parser):
        parser.add_argument("--country-code", default="LT")
        parser.add_argument("--city", default="")
        parser.add_argument("--limit", type=int, default=10000)
        parser.add_argument("--deactivate-missing", action="store_true")

    def handle(self, *args, **opts):
        country_code = str(opts["country_code"] or "LT").strip().upper()
        city = str(opts["city"] or "").strip()
        limit = int(opts["limit"] or 10000)
        deactivate_missing = bool(opts["deactivate_missing"])

        params: dict = {"countryCode": country_code, "limit": limit}
        if city:
            params["city"] = city

        try:
            items = DpdClient().list_lockers(params=params)
        except DpdApiError as e:
            raise SystemExit(str(e))

        seen_ids: set[str] = set()
        created = 0
        updated = 0

        for it in items:
            if not isinstance(it, dict):
                continue
            locker_id = str(it.get("id") or it.get("lockerId")
                            or it.get("code") or "").strip()
            if not locker_id:
                continue
            seen_ids.add(locker_id)

            addr = it.get("address")
            addr = addr if isinstance(addr, dict) else {}

            obj, was_created = DpdLocker.objects.get_or_create(
                locker_id=locker_id)

            obj.country_code = str(
                it.get("countryCode")
                or addr.get("country")
                or country_code
                or ""
            ).strip().upper()
            obj.city = str(it.get("city") or addr.get("city") or "").strip()
            obj.name = str(it.get("name") or "").strip()
            obj.street = str(
                it.get("street")
                or addr.get("street")
                or it.get("address")
                or ""
            ).strip()
            obj.postal_code = str(
                it.get("postalCode")
                or addr.get("postalCode")
                or addr.get("postal_code")
                or ""
            ).strip()

            lat = it.get("latitude")
            lng = it.get("longitude")
            if lat is None or lng is None:
                lat_lng = addr.get("latLong")
                if isinstance(lat_lng, (list, tuple)) and len(lat_lng) >= 2:
                    lat = lat if lat is not None else lat_lng[0]
                    lng = lng if lng is not None else lat_lng[1]
            obj.latitude = Decimal(str(lat)) if lat is not None else None
            obj.longitude = Decimal(str(lng)) if lng is not None else None

            obj.raw = it
            obj.is_active = True

            if was_created:
                created += 1
            else:
                updated += 1
            obj.save()

        if deactivate_missing and not city:
            # Only safe to deactivate when doing a full-country sync.
            DpdLocker.objects.filter(country_code=country_code).exclude(
                locker_id__in=seen_ids).update(is_active=False)

        self.stdout.write(
            self.style.SUCCESS(
                f"DPD lockers synced: created={created}, updated={updated}, total_seen={len(seen_ids)}"
            )
        )
