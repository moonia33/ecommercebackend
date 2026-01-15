from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import requests
from django.conf import settings


@dataclass(frozen=True)
class MeiliConfig:
    host: str
    api_key: str


class MeiliError(RuntimeError):
    pass


def _get_cfg() -> MeiliConfig:
    host = str(getattr(settings, "MEILI_HOST", "") or "").strip().rstrip("/")
    api_key = str(getattr(settings, "MEILI_API_KEY", "") or "").strip()
    return MeiliConfig(host=host, api_key=api_key)


class MeiliClient:
    def __init__(self) -> None:
        self.cfg = _get_cfg()

    def enabled(self) -> bool:
        return bool(getattr(settings, "MEILI_ENABLED", False)) and bool(self.cfg.host)

    def _headers(self) -> dict[str, str]:
        h = {"Content-Type": "application/json", "Accept": "application/json"}
        if self.cfg.api_key:
            h["Authorization"] = f"Bearer {self.cfg.api_key}"
        return h

    def health(self) -> dict[str, Any]:
        if not self.cfg.host:
            raise MeiliError("MEILI_HOST is not configured")
        r = requests.get(f"{self.cfg.host}/health", headers=self._headers(), timeout=10)
        if r.status_code >= 400:
            raise MeiliError(f"Meili health failed: {r.status_code} {r.text[:300]}")
        return r.json()

    def create_index(self, *, uid: str, primary_key: str = "id") -> dict[str, Any]:
        r = requests.post(
            f"{self.cfg.host}/indexes",
            json={"uid": uid, "primaryKey": primary_key},
            headers=self._headers(),
            timeout=30,
        )
        if r.status_code in {200, 201, 202}:
            return r.json()
        if r.status_code == 409:
            return {"uid": uid, "primaryKey": primary_key}
        raise MeiliError(f"Meili create index failed: {r.status_code} {r.text[:300]}")

    def update_settings(self, *, uid: str, settings_payload: dict[str, Any]) -> dict[str, Any]:
        r = requests.patch(
            f"{self.cfg.host}/indexes/{uid}/settings",
            json=settings_payload,
            headers=self._headers(),
            timeout=60,
        )
        if r.status_code >= 400:
            raise MeiliError(f"Meili update settings failed: {r.status_code} {r.text[:300]}")
        return r.json()

    def add_documents(self, *, uid: str, documents: list[dict[str, Any]]) -> dict[str, Any]:
        r = requests.post(
            f"{self.cfg.host}/indexes/{uid}/documents",
            json=documents,
            headers=self._headers(),
            timeout=120,
        )
        if r.status_code >= 400:
            raise MeiliError(f"Meili add documents failed: {r.status_code} {r.text[:300]}")
        return r.json()

    def delete_all_documents(self, *, uid: str) -> dict[str, Any]:
        r = requests.delete(
            f"{self.cfg.host}/indexes/{uid}/documents",
            headers=self._headers(),
            timeout=60,
        )
        if r.status_code >= 400:
            raise MeiliError(f"Meili delete documents failed: {r.status_code} {r.text[:300]}")
        return r.json()

    def wait_for_task(self, *, task_uid: int, timeout_seconds: int = 120) -> dict[str, Any]:
        import time

        deadline = time.time() + max(1, int(timeout_seconds))
        last = None
        while time.time() < deadline:
            r = requests.get(
                f"{self.cfg.host}/tasks/{int(task_uid)}",
                headers=self._headers(),
                timeout=20,
            )
            if r.status_code >= 400:
                raise MeiliError(f"Meili task status failed: {r.status_code} {r.text[:300]}")
            last = r.json()
            status = str(last.get("status") or "")
            if status in {"succeeded", "failed", "canceled"}:
                return last
            time.sleep(0.25)
        return last or {}

    def search(self, *, uid: str, payload: dict[str, Any]) -> dict[str, Any]:
        r = requests.post(
            f"{self.cfg.host}/indexes/{uid}/search",
            json=payload,
            headers=self._headers(),
            timeout=30,
        )
        if r.status_code >= 400:
            raise MeiliError(f"Meili search failed: {r.status_code} {r.text[:300]}")
        return r.json()
