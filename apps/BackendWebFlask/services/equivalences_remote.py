"""MS-12 (ms-kits-charolas): equivalent instrument families authorized for a kit, Supervisor screen.

CatalogService owns the data (/api/v1/catalog/kits/{id}/equivalences): SPD-only writes, institution scope, active
kit, rules, history on revocation and audit are enforced there. The portal forwards the verified token and adapts
messages. An equivalence never changes expected quantities, the reference or the final-count rule.
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
ERROR_MESSAGES = {
    "kit_inactive": N_("Equivalences are authorized on the active version of a kit."),
    "family_not_in_kit": N_("Choose an instrument family of this kit."),
    "equivalent_in_kit": N_("The equivalent family is already part of the kit."),
    "equivalent_unavailable": N_("The equivalent family does not exist or is inactive."),
    "same_family": N_("A family cannot be equivalent to itself."),
    "equivalence_exists": N_("This equivalence is already authorized for the kit."),
}
UNAVAILABLE = N_("The catalog service is temporarily unavailable. Try again in a moment.")


class EquivalenceError(Exception):
    def __init__(self, message: str, status: int):
        super().__init__(message)
        self.message, self.status = message, status


def enabled() -> bool:
    return bool(CATALOG_SERVICE_URL)


def _call(method: str, path: str, *, json: Any = None, params: dict | None = None) -> Any:
    token = getattr(g, "auth_access_token", None)
    if not token:
        raise EquivalenceError(N_("Your session expired. Sign in again."), 401)
    try:
        response = requests.request(method, f"{CATALOG_SERVICE_URL}/api/v1/catalog{path}", json=json, params=params,
                                    timeout=TIMEOUT, headers={"Authorization": f"Bearer {token}",
                                                              "Accept": "application/json",
                                                              "X-Correlation-ID": uuid.uuid4().hex})
    except requests.RequestException:
        raise EquivalenceError(UNAVAILABLE, 503) from None
    if response.status_code >= 500:
        raise EquivalenceError(UNAVAILABLE, 503)
    try:
        body = response.json()
    except ValueError:
        body = {}
    if response.status_code >= 400:
        body = body if isinstance(body, dict) else {}
        reason = (body.get("details") or {}).get("reason") or body.get("error")
        message = ERROR_MESSAGES.get(reason) or body.get("message") or N_("The equivalence could not be saved.")
        raise EquivalenceError(message, response.status_code)
    return body


def active_kits() -> list[dict[str, Any]]:
    return [k for k in _call("GET", "/kits") if k.get("active")]


def kit(kit_id: str) -> dict[str, Any]:
    return _call("GET", f"/kits/{kit_id}", params={"expand": "detail"})


def active_families() -> list[dict[str, Any]]:
    return [f for f in _call("GET", "/families") if f.get("active")]


def equivalences(kit_id: str) -> list[dict[str, Any]]:
    return _call("GET", f"/kits/{kit_id}/equivalences", params={"status": "all"})


def authorize(kit_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    return _call("POST", f"/kits/{kit_id}/equivalences", json=payload)


def revoke(kit_id: str, equivalence_id: str) -> dict[str, Any]:
    return _call("DELETE", f"/kits/{kit_id}/equivalences/{equivalence_id}")
