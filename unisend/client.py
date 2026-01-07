from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Any

import requests
from django.conf import settings
from django.utils import timezone


@dataclass(frozen=True)
class UnisendHttpConfig:
    base_url: str


class UnisendApiError(RuntimeError):
    pass


def _get_base_url() -> str:
    base_url = ""
    try:
        from .models import UnisendApiConfig as UnisendDbConfig

        cfg = UnisendDbConfig.objects.order_by("id").first()
        if cfg:
            base_url = str(cfg.base_url or "").strip().rstrip("/")
    except Exception:
        pass

    if not base_url:
        base_url = str(getattr(settings, "UNISEND_BASE_URL", "https://api-manosiuntos.post.lt")).strip().rstrip("/")
    return base_url


class UnisendClient:
    def __init__(self) -> None:
        self.base_url = _get_base_url()

    def _get_db_cfg(self):
        from .models import UnisendApiConfig

        cfg = UnisendApiConfig.get_solo()
        return cfg

    def _auth_headers(self, *, token: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {token}", "Accept": "application/json"}

    def _ensure_token(self) -> str:
        cfg = self._get_db_cfg()

        now = timezone.now()
        if cfg.access_token and cfg.token_expires_at and cfg.token_expires_at > now + timedelta(seconds=30):
            return cfg.access_token

        if cfg.refresh_token:
            try:
                data = self.refresh_token(refresh_token=cfg.refresh_token)
                access = str(data.get("access_token") or "").strip()
                refresh = str(data.get("refresh_token") or "").strip()
                expires_in = int(data.get("expires_in") or 0)
                if access:
                    cfg.access_token = access
                    if refresh:
                        cfg.refresh_token = refresh
                    if expires_in > 0:
                        cfg.token_expires_at = timezone.now() + timedelta(seconds=expires_in)
                    cfg.save(update_fields=["access_token", "refresh_token", "token_expires_at", "updated_at"])
                    return access
            except Exception:
                pass

        data = self.password_token(username=cfg.username, password=cfg.password, client_system=cfg.client_system)
        access = str(data.get("access_token") or "").strip()
        refresh = str(data.get("refresh_token") or "").strip()
        expires_in = int(data.get("expires_in") or 0)
        if not access:
            raise UnisendApiError("Unisend: nepavyko gauti access_token")

        cfg.access_token = access
        if refresh:
            cfg.refresh_token = refresh
        if expires_in > 0:
            cfg.token_expires_at = timezone.now() + timedelta(seconds=expires_in)
        cfg.save(update_fields=["access_token", "refresh_token", "token_expires_at", "updated_at"])
        return access

    def password_token(self, *, username: str, password: str, client_system: str = "PUBLIC") -> dict[str, Any]:
        url = f"{self.base_url}/oauth/token"
        params = {
            "scope": "read+write+API_CLIENT",
            "grant_type": "password",
            "clientSystem": client_system or "PUBLIC",
            "username": username,
            "password": password,
        }
        r = requests.post(url, params=params, timeout=30)
        if r.status_code >= 400:
            raise UnisendApiError(f"Unisend token failed: {r.status_code} {r.text[:300]}")
        data = r.json()
        if not isinstance(data, dict):
            raise UnisendApiError("Unisend token: unexpected response")
        return data

    def refresh_token(self, *, refresh_token: str, client_system: str = "PUBLIC") -> dict[str, Any]:
        url = f"{self.base_url}/oauth/token"
        params = {
            "scope": "read+write",
            "grant_type": "refresh_token",
            "clientSystem": client_system or "PUBLIC",
            "refresh_token": refresh_token,
        }
        r = requests.post(url, params=params, timeout=30)
        if r.status_code >= 400:
            raise UnisendApiError(f"Unisend refresh token failed: {r.status_code} {r.text[:300]}")
        data = r.json()
        if not isinstance(data, dict):
            raise UnisendApiError("Unisend refresh token: unexpected response")
        return data

    def list_terminals(
        self,
        *,
        receiver_country_code: str,
        find: str | None = None,
        size: int | None = None,
    ) -> Any:
        token = self._ensure_token()
        url = f"{self.base_url}/api/v2/terminal"
        params: dict[str, Any] = {"receiverCountryCode": str(receiver_country_code or "").strip().upper()}
        if find:
            params["find"] = str(find).strip()
        if size is not None:
            params["size"] = int(size)
        r = requests.get(url, params=params, headers=self._auth_headers(token=token), timeout=30)
        if r.status_code >= 400:
            raise UnisendApiError(f"Unisend terminals failed: {r.status_code} {r.text[:300]}")
        return r.json()

    def create_parcel(self, *, payload: dict[str, Any]) -> dict[str, Any]:
        token = self._ensure_token()
        url = f"{self.base_url}/api/v2/parcel"
        r = requests.post(url, json=payload, headers={**self._auth_headers(token=token), "Content-Type": "application/json"}, timeout=30)
        if r.status_code >= 400:
            raise UnisendApiError(f"Unisend parcel create failed: {r.status_code} {r.text[:300]}")
        data = r.json()
        if not isinstance(data, dict):
            raise UnisendApiError("Unisend parcel create: unexpected response")
        return data

    def initiate_shipping(self, *, parcel_ids: list[int], process_async: bool = False) -> dict[str, Any]:
        token = self._ensure_token()
        url = f"{self.base_url}/api/v2/shipping/initiate"
        params = {"processAsync": str(bool(process_async)).lower()}
        payload = {"parcelIds": parcel_ids}
        r = requests.post(url, params=params, json=payload, headers={**self._auth_headers(token=token), "Content-Type": "application/json"}, timeout=60)
        if r.status_code >= 400:
            raise UnisendApiError(f"Unisend shipping initiate failed: {r.status_code} {r.text[:300]}")
        data = r.json()
        if not isinstance(data, dict):
            raise UnisendApiError("Unisend shipping initiate: unexpected response")
        return data

    def list_barcodes(self, *, parcel_ids: list[int]) -> Any:
        token = self._ensure_token()
        url = f"{self.base_url}/api/v2/shipping/barcode/list"
        params: dict[str, Any] = {"parcelIds": [int(x) for x in parcel_ids]}
        r = requests.get(url, params=params, headers=self._auth_headers(token=token), timeout=30)
        if r.status_code >= 400:
            raise UnisendApiError(f"Unisend barcode list failed: {r.status_code} {r.text[:300]}")
        return r.json()

    def get_sticker_pdf(
        self,
        *,
        parcel_ids: list[int],
        layout: str = "LAYOUT_10x15",
        label_orientation: str = "PORTRAIT",
        include_cn23: bool = False,
        include_manifest: bool = False,
    ) -> bytes:
        token = self._ensure_token()
        url = f"{self.base_url}/api/v2/sticker/pdf"
        params: dict[str, Any] = {
            "parcelIds": [int(x) for x in parcel_ids],
            "layout": layout,
            "labelOrientation": label_orientation,
            "includeCn23": str(bool(include_cn23)).lower(),
            "includeManifest": str(bool(include_manifest)).lower(),
        }
        r = requests.get(url, params=params, headers={"Authorization": f"Bearer {token}", "Accept": "application/pdf"}, timeout=60)
        if r.status_code >= 400:
            raise UnisendApiError(f"Unisend sticker pdf failed: {r.status_code} {r.text[:300]}")
        return r.content
