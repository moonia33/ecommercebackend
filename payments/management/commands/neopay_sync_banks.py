from __future__ import annotations

from django.core.management.base import BaseCommand
from django.utils import timezone

from payments.models import NeopayBank
from payments.services.neopay import get_neopay_config


class Command(BaseCommand):
    help = "Sync Neopay banks into local DB for fast checkout and admin control."

    def add_arguments(self, parser):
        parser.add_argument("--country-code", default="")
        parser.add_argument("--limit", type=int, default=0)
        parser.add_argument("--deactivate-missing", action="store_true")

    def handle(self, *args, **opts):
        cfg = get_neopay_config()
        if not cfg:
            raise SystemExit("Neopay config is not set")
        if not cfg.enable_bank_preselect:
            raise SystemExit("Neopay bank preselect is disabled")

        cc_filter = str(opts.get("country_code") or "").strip().upper()
        limit = int(opts.get("limit") or 0)
        deactivate_missing = bool(opts.get("deactivate_missing"))

        if (cfg.force_bank_bic or "").strip():
            raise SystemExit("force_bank_bic is set; syncing banks list is not applicable")

        import requests

        base = (cfg.banks_api_base_url or "https://psd2.neopay.lt/api").rstrip("/")
        if base.endswith("/countries"):
            base = base[: -len("/countries")]

        candidates = [
            f"{base}/countries/{cfg.project_id}",
            f"{base}/countries/{cfg.project_id}/",
            f"{base}/countries",
            f"{base}/countries/",
        ]
        if base.endswith("/api"):
            root = base[: -len("/api")]
            candidates.append(f"{root}/api/countries/{cfg.project_id}")
            candidates.append(f"{root}/api/countries/{cfg.project_id}/")

        data = None
        last_response = None
        last_exc: Exception | None = None

        for url in candidates:
            try:
                r = requests.get(
                    url,
                    timeout=20,
                    headers={
                        "Accept": "application/json",
                        "User-Agent": "inultimo-backend/1.0",
                    },
                )
            except requests.RequestException as e:
                last_exc = e
                continue

            last_response = r
            if r.status_code == 404:
                continue
            if r.status_code >= 400:
                body = (r.text or "").strip().replace("\n", " ")
                if len(body) > 300:
                    body = body[:300] + "..."
                raise SystemExit(f"Neopay banks api failed: {r.status_code} url={url} body={body}")

            try:
                data = r.json()
            except Exception:
                continue
            break

        if data is None:
            if last_exc is not None:
                raise SystemExit(f"Neopay banks api request failed: {type(last_exc).__name__}")
            if last_response is not None:
                body = (last_response.text or "").strip().replace("\n", " ")
                if len(body) > 300:
                    body = body[:300] + "..."
                raise SystemExit(f"Neopay banks api failed: {last_response.status_code} url={candidates[-1]} body={body}")
            raise SystemExit("Neopay banks api failed: no response")

        if isinstance(data, list):
            countries = data
        elif isinstance(data, dict):
            countries = data.get("countries") or []
            if not countries and cc_filter:
                if cc_filter in data and isinstance(data.get(cc_filter), dict):
                    countries = [{"code": cc_filter, **data.get(cc_filter)}]
        else:
            countries = []

        if not isinstance(countries, list) or not countries:
            raise SystemExit("Neopay banks sync: unexpected response")

        seen: dict[str, set[str]] = {}
        created = 0
        updated = 0
        synced_at = timezone.now()

        for c in countries:
            if not isinstance(c, dict):
                continue
            ccode = (c.get("code") or c.get("country") or c.get("countryCode") or "").strip().upper()
            if not ccode:
                continue
            if cc_filter and ccode != cc_filter:
                continue

            banks = c.get("aspsps") or c.get("banks") or []
            if not isinstance(banks, list):
                continue

            if ccode not in seen:
                seen[ccode] = set()

            for b in banks:
                if not isinstance(b, dict):
                    continue
                bic = (b.get("bic") or b.get("BIC") or "").strip()
                if not bic:
                    continue

                services = b.get("services") or b.get("serviceTypes") or []
                if isinstance(services, str):
                    services = [services]
                if not isinstance(services, list):
                    services = []
                if "pisp" not in {str(x).strip().lower() for x in services}:
                    continue

                seen[ccode].add(bic)

                name = (b.get("name") or b.get("bankName") or "").strip() or bic
                logo_url = (b.get("logo") or b.get("logoUrl") or "").strip() if isinstance(b.get("logo") or b.get("logoUrl") or "", str) else ""
                is_operating = bool(b.get("isOperating")) if "isOperating" in b else True

                obj, was_created = NeopayBank.objects.get_or_create(
                    country_code=ccode,
                    bic=bic,
                    defaults={"is_enabled": True},
                )

                obj.name = name
                obj.logo_url = logo_url
                obj.is_operating = is_operating
                obj.raw = b
                obj.last_synced_at = synced_at

                obj.save(update_fields=["name", "logo_url", "is_operating", "raw", "last_synced_at", "updated_at"])

                if was_created:
                    created += 1
                else:
                    updated += 1

                if limit and (created + updated) >= limit:
                    break

        if deactivate_missing and cc_filter:
            # Only safe to mark missing when syncing a single full country.
            NeopayBank.objects.filter(country_code=cc_filter).exclude(bic__in=seen.get(cc_filter, set())).update(is_operating=False)

        total_seen = sum(len(v) for v in seen.values())
        self.stdout.write(
            self.style.SUCCESS(
                f"Neopay banks synced: created={created}, updated={updated}, total_seen={total_seen} country={cc_filter or 'ALL'}"
            )
        )
