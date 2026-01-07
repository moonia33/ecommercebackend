from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import requests
from django.conf import settings


@dataclass(frozen=True)
class DpdConfig:
    base_url: str
    token: str
    status_lang: str = "lt"


class DpdApiError(RuntimeError):
    pass


def _get_cfg() -> DpdConfig:
    base_url = ""
    token = ""
    status_lang = ""

    try:
        from .models import DpdConfig as DpdDbConfig

        cfg = DpdDbConfig.objects.order_by("id").first()
        if cfg:
            base_url = str(cfg.base_url or "").strip().rstrip("/")
            token = str(cfg.token or "").strip()
            status_lang = str(cfg.status_lang or "").strip()
    except Exception:
        # DB might not be ready during startup/migrations.
        pass

    if not base_url:
        base_url = str(
            getattr(settings, "DPD_BASE_URL", "https://esiunta.dpd.lt/api/v1")
        ).rstrip("/")
    if not token:
        token = str(getattr(settings, "DPD_TOKEN", "")).strip()
    if not status_lang:
        status_lang = str(
            getattr(settings, "DPD_STATUS_LANG", "lt")).strip() or "lt"
    return DpdConfig(base_url=base_url, token=token, status_lang=status_lang)


class DpdClient:
    def __init__(self) -> None:
        self.cfg = _get_cfg()

    def _headers(self, *, accept: str = "application/json") -> dict[str, str]:
        headers = {
            "Accept": accept,
        }
        if self.cfg.token:
            headers["Authorization"] = f"Bearer {self.cfg.token}"
        return headers

    def list_lockers(self, *, params: dict[str, Any]) -> list[dict[str, Any]]:
        url = f"{self.cfg.base_url}/lockers/"
        r = requests.get(url, params=params,
                         headers=self._headers(), timeout=20)
        if r.status_code >= 400:
            raise DpdApiError(
                f"DPD lockers failed: {r.status_code} {r.text[:300]}")
        data = r.json()
        if not isinstance(data, list):
            raise DpdApiError("DPD lockers: unexpected response")
        return data

    def get_status(self, *, pknr: str, detail: str = "0", show_all: str = "0") -> list[dict[str, Any]]:
        url = f"{self.cfg.base_url}/status/tracking"
        params = {
            "pknr": pknr,
            "detail": detail,
            "show_all": show_all,
            "lang": self.cfg.status_lang,
        }
        r = requests.get(url, params=params,
                         headers=self._headers(), timeout=20)
        if r.status_code >= 400:
            raise DpdApiError(
                f"DPD status failed: {r.status_code} {r.text[:300]}")
        data = r.json()
        if not isinstance(data, list):
            raise DpdApiError("DPD status: unexpected response")
        return data

    def create_shipments(self, *, shipments: list[dict[str, Any]]) -> list[dict[str, Any]]:
        url = f"{self.cfg.base_url}/shipments"
        r = requests.post(
            url,
            json=shipments,
            headers={
                **self._headers(accept="application/json"),
                "Content-Type": "application/json",
            },
            timeout=30,
        )
        if r.status_code >= 400:
            raise DpdApiError(
                f"DPD create shipments failed: {r.status_code} {r.text[:300]}"
            )
        data = r.json()
        if not isinstance(data, list):
            raise DpdApiError("DPD create shipments: unexpected response")
        return data

    def get_shipments(self, *, ids: list[str]) -> list[dict[str, Any]]:
        url = f"{self.cfg.base_url}/shipments"
        params: dict[str, Any] = {}
        if ids:
            params["ids[]"] = [str(x).strip() for x in ids if str(x).strip()]
        r = requests.get(url, params=params, headers=self._headers(), timeout=20)
        if r.status_code >= 400:
            raise DpdApiError(
                f"DPD shipments failed: {r.status_code} {r.text[:300]}"
            )
        data = r.json()
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            items = data.get("items")
            if isinstance(items, list):
                return items
        raise DpdApiError("DPD shipments: unexpected response")

    def create_labels_pdf(self, *, payload: dict[str, Any], endpoint: str = "/shipments/labels") -> bytes:
        endpoint = endpoint if endpoint.startswith("/") else f"/{endpoint}"
        url = f"{self.cfg.base_url}{endpoint}"
        r = requests.post(
            url,
            json=payload,
            headers={
                **self._headers(accept="application/pdf"),
                "Content-Type": "application/json",
            },
            timeout=60,
        )
        if r.status_code >= 400:
            raise DpdApiError(
                f"DPD labels failed: {r.status_code} {r.text[:300]}"
            )
        return r.content

    def list_services(
        self,
        *,
        country_from: str,
        country_to: str,
        postal_code_from: str | None = None,
        postal_code_to: str | None = None,
        service_type: str | None = None,
        main_service_alias: str | None = None,
        main_service_name: str | None = None,
        payer_code: str | int | None = None,
        package_size: str | None = None,
    ) -> Any:
        url = f"{self.cfg.base_url}/services"
        params: dict[str, Any] = {
            "countryFrom": str(country_from or "").strip(),
            "countryTo": str(country_to or "").strip(),
        }
        if postal_code_from:
            params["postalCodeFrom"] = str(postal_code_from).strip()
        if postal_code_to:
            params["postalCodeTo"] = str(postal_code_to).strip()
        if service_type:
            params["serviceType"] = str(service_type).strip()
        if main_service_alias:
            params["mainServiceAlias"] = str(main_service_alias).strip()
        if main_service_name:
            params["mainServiceName"] = str(main_service_name).strip()
        if payer_code is not None and str(payer_code).strip():
            params["payerCode"] = str(payer_code).strip()
        if package_size:
            params["packageSize"] = str(package_size).strip()

        r = requests.get(url, params=params, headers=self._headers(), timeout=20)
        if r.status_code >= 400:
            raise DpdApiError(f"DPD services failed: {r.status_code} {r.text[:300]}")
        return r.json()
