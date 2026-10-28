"""HTTP client of EvidenceService (PEF-DS09 §1.3.5 / PEF-DS05) used by the checkpoint adapter.

Only the existing contract is used: POST /api/v1/evidence (multipart session_id, kind, file) with
the Operator's Bearer access token, and GET /api/v1/sessions/{id}/evidence to find an asset that
a previous, interrupted attempt already stored (EvidenceService has no idempotency key: the
SHA-256 of the exact bytes identifies our own earlier upload).
"""

from __future__ import annotations

import hashlib
import os

import requests

EVIDENCE_SERVICE_URL = os.getenv("EVIDENCE_SERVICE_URL", "").rstrip("/")
EVIDENCE_TIMEOUT = float(os.getenv("EVIDENCE_SERVICE_TIMEOUT", "5"))


class EvidenceError(Exception):
    """retryable: the service may accept the same upload later (timeouts, 5xx, 503 dependency)."""

    def __init__(self, message: str, *, retryable: bool, status: int | None = None):
        super().__init__(message)
        self.retryable = retryable
        self.status = status


def configured() -> bool:
    return bool(EVIDENCE_SERVICE_URL)


def _headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}", "Accept": "application/json"}


def _raise_for(response: requests.Response) -> None:
    if response.status_code < 400:
        return
    retryable = response.status_code >= 500 or response.status_code == 429
    raise EvidenceError(f"EvidenceService HTTP {response.status_code}", retryable=retryable,
                        status=response.status_code)


def find_by_sha256(session_id: str, sha256: str, token: str) -> dict | None:
    try:
        response = requests.get(f"{EVIDENCE_SERVICE_URL}/api/v1/sessions/{session_id}/evidence",
                                headers=_headers(token), timeout=EVIDENCE_TIMEOUT)
    except requests.RequestException as exc:
        raise EvidenceError(f"EvidenceService unreachable: {type(exc).__name__}", retryable=True) from exc
    _raise_for(response)
    body = response.json()
    items = body.get("evidence_list") if isinstance(body, dict) else body
    if isinstance(items, dict):
        items = items.get("items") or items.get("evidence") or []
    for item in items or []:
        if isinstance(item, dict) and item.get("sha256") == sha256:
            return item
    return None


def upload_jpeg(session_id: str, jpeg: bytes, token: str) -> dict:
    """Store one JPEG frame; returns the Evidence item (id, sha256, retention_until, ...)."""
    sha256 = hashlib.sha256(jpeg).hexdigest()
    try:
        response = requests.post(
            f"{EVIDENCE_SERVICE_URL}/api/v1/evidence",
            headers=_headers(token),
            data={"session_id": session_id, "kind": "jpeg"},
            files={"file": ("checkpoint.jpg", jpeg, "image/jpeg")},
            timeout=EVIDENCE_TIMEOUT,
        )
    except requests.Timeout as exc:
        # The request may have reached the service: the caller looks it up by SHA-256 first.
        raise EvidenceError("EvidenceService timeout", retryable=True) from exc
    except requests.RequestException as exc:
        raise EvidenceError(f"EvidenceService unreachable: {type(exc).__name__}", retryable=True) from exc
    _raise_for(response)
    body = response.json()
    item = body.get("evidence", body) if isinstance(body, dict) else {}
    if item.get("sha256") != sha256:
        raise EvidenceError("EvidenceService returned a different SHA-256", retryable=False)
    return item
