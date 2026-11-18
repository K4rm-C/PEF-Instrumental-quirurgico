"""MS-08: physical trays for the Supervisor screens — CatalogService (/api/v1/catalog/trays, owner) and
SessionService (/api/v1/sessions/trays/{id}, sessions that used a tray). The portal forwards the verified
token; every rule (SPD only writes, institution, active kit, version conflicts, history, audit) is enforced by
the services. Nothing is computed here.
"""
from __future__ import annotations

import os
import uuid
from typing import Any

import requests
from flask import g

from i18n import N_

CATALOG_SERVICE_URL = os.getenv("CATALOG_SERVICE_URL", "").strip().rstrip("/")
TIMEOUT = float(os.getenv("CATALOG_SERVICE_TIMEOUT", "5"))
STATUS_LABELS = {"active": N_("Active"), "out_of_service": N_("Out of service"), "retired": N_("Retired")}
STATUS_VARIANTS = {"active": "success", "out_of_service": "warning", "retired": "neutral"}
REASON_MESSAGES = {
    "tray_code_taken": N_("A tray with this code already exists."),
    "invalid_code": N_("The tray code is required (letters, digits, . _ - /; max 64 characters)."),
    "invalid_kit": N_("Choose a valid kit."),
    "inactive_kit": N_("Only an active kit version can be assigned to a tray."),
    "invalid_status": N_("Choose a valid status."),
}
UNAVAILABLE = N_("The catalog service is temporarily unavailable. Try again in a moment.")


class TrayError(Exception):
    def __init__(self, message: str, status: int, *, current_version: int | None = None):
        super().__init__(message)
        self.message, self.status, self.current_version = message, status, current_version


def enabled() -> bool:
    return bool(CATALOG_SERVICE_URL)


def _call(method: str, path: str, *, json: Any = None, params: dict | None = None) -> Any:
    token = getattr(g, "auth_access_token", None)
    if not token:
        raise TrayError(N_("Your session expired. Sign in again."), 401)
    try:
        response = requests.request(method, f"{CATALOG_SERVICE_URL}/api/v1/catalog{path}", json=json, params=params,
                                    timeout=TIMEOUT, headers={"Authorization": f"Bearer {token}",
                                                              "Accept": "application/json",
                                                              "X-Correlation-ID": uuid.uuid4().hex})
    except requests.RequestException:
        raise TrayError(UNAVAILABLE, 503) from None
    if response.status_code >= 500:
        raise TrayError(UNAVAILABLE, 503)
    try:
        body = response.json()
    except ValueError:
        body = {}
    if response.status_code >= 400:
        details = (body or {}).get("details") or {}
        if body.get("error") == "version_conflict":
            raise TrayError(N_("Someone else changed this tray. The current values are shown; review them and save again."),
                            409, current_version=details.get("current_version"))
        message = REASON_MESSAGES.get(details.get("reason")) or body.get("message") or N_("The tray could not be saved.")
        raise TrayError(message, response.status_code)
    return body


def trays(status: str | None = None) -> list[dict[str, Any]]:
    return _call("GET", "/trays", params={"status": status} if status else None)


def tray(tray_id: str) -> dict[str, Any]:
    return _call("GET", f"/trays/{tray_id}")


def create(payload: dict[str, Any]) -> dict[str, Any]:
    return _call("POST", "/trays", json=payload)


def update(tray_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    return _call("PUT", f"/trays/{tray_id}", json=payload)


def kits() -> list[dict[str, Any]]:
    return [k for k in _call("GET", "/kits") if k.get("active")]
