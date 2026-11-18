"""Supervisor session operations served by SessionService (HTTP, PEF-DS09 §1.3.4 complement v0.2.0).

When SESSION_SERVICE_URL is configured, controllers/supervisor_sessions.py sends here every
Supervisor operation on sessions: schedule, full edit, privacy agreement, expected inventory and
confirmed additions (instruments_added), discrepancy queue and resolution, SPD close, and the reads
(`expand=detail` snapshots) the Supervisor screens are built from. The token is the one AuthService
already verified for this request; SessionService validates it again (JWT, Redis session, active
user, effective roles) — no header is trusted as identity.

Exactly one path per request: no local fallback and no double write. If SessionService does not
answer, the Supervisor gets a controlled 503 message. The Operator's counting flow stays in
services/rf_session.py on PostgreSQL; the only Operator call here is the Operator close of a
reconciled session (operator_close), which SessionService authorises and executes.

Errors keep the RF messages: SessionService answers a stable details.reason that maps to the same
msgid the local implementation raised (REASON_MESSAGES).
"""

from __future__ import annotations

import os
import uuid
from typing import Any

import requests
from flask import g

from i18n import N_
from services.rf_session import RfSessionError

SESSION_SERVICE_URL = os.getenv("SESSION_SERVICE_URL", "").strip().rstrip("/")
SESSION_TIMEOUT = float(os.getenv("SESSION_SERVICE_TIMEOUT", "5"))
BASE = "/api/v1/sessions"
UNAVAILABLE = N_("The session service is temporarily unavailable. Try again in a moment.")

REASON_MESSAGES = {
    "session_not_found": N_("Session not found."),
    "invalid_operator": N_("Choose a valid operator."),
    "operator_role_required": N_("Assigned user must have station_operator role."),
    "invalid_room": N_("Choose a valid operating room."),
    "invalid_station": N_("Choose a capture station in the selected operating room."),
    "invalid_kit": N_("Choose a valid kit."),
    "invalid_patient": N_("Choose a valid patient."),
    "invalid_procedure": N_("Choose a valid procedure."),
    "physicians_required": N_("At least one physician is required."),
    "physician_required": N_("Each surgical team row needs a physician."),
    "duplicate_physician": N_("Each physician can only be selected once."),
    "invalid_physician": N_("Choose a valid physician."),
    "invalid_phase": N_("Invalid initial phase."),
    "inactive_room": N_("This operating room is inactive. Choose an active room."),
    "inactive_station": N_("This capture station is inactive. Choose an active station."),
    "inactive_kit": N_("This kit is inactive or superseded. Choose an active kit."),
    "inactive_phase": N_("This operation phase is inactive. Choose an active phase."),
    "lines_required": N_("Provide at least one inventory line."),
    "invalid_quantity": N_("Expected quantities must be >= 1."),
    "stock_block": N_("Stock BLOCK: requested quantity exceeds available instruments."),
    "not_scheduled": N_("Full session edit is only available while scheduled."),
    "privacy_not_scheduled": N_("Privacy agreement can only be set while session is scheduled."),
    "capture_mode_frozen": N_("Capture mode already frozen; cannot change privacy agreement."),
    "invalid_privacy_notice": N_("Choose an active privacy notice version."),
    "inventory_locked": N_("Inventory can only be modified while scheduled or in progress."),
    "notes_required": N_("Resolution notes are required."),
    "discrepancy_not_found": N_("Discrepancy not found."),
    "already_resolved": N_("Discrepancy already resolved."),
    "session_not_reviewable": N_("Discrepancies can only be resolved after the Operator finished and before the SPD close."),
    "material_not_recovered": N_("Confirm material recovered / reprocessing started."),
    "not_awaiting_spd_review": N_("Session is not awaiting SPD review."),
    "open_discrepancies": N_("Resolve open discrepancies before closing."),
    "contingency_review_required": N_("Confirm that you reviewed the contingency count before closing."),
    "physical_shortage_needs_piece_outcome": N_("A physically missing instrument cannot be resolved with notes only. "
                                                "Record the piece as lost; the incident stays unreconciled."),
    # MS-09 (same messages as the in-process close)
    "unverified_contingency": N_("The contingency reason was declared by the Operator and could not be verified by the "
                                 "system. Acknowledge it explicitly before closing."),
    "final_count_escalation": N_("The Operator reported a possible physical difference after a rejected final count. "
                                 "Acknowledge that the session is closed without a valid final count."),
    "unreconciled_physical_shortage": N_("This session has a physically missing instrument that was not documented "
                                         "as recovered. Acknowledge that it is closed as an unreconciled shortage."),
    "physical_integrity_not_confirmed": N_("Confirm that the physical instrument set is complete."),
    "not_assigned_operator": N_("Session is not assigned to you."),
    "already_closed": N_("Session is already closed."),
    "operator_close_not_allowed": N_("This session does not meet the conditions for an Operator close. It stays "
                                     "in Supervisor review."),
}


class SessionServiceUnavailable(RfSessionError):
    """SessionService did not answer (network, timeout, 5xx): controlled 503, nothing written."""

    def __init__(self, detail: str = ""):
        super().__init__(UNAVAILABLE, 503)
        self.detail = detail


def enabled() -> bool:
    return bool(SESSION_SERVICE_URL)


def _correlation_id() -> str:
    if not getattr(g, "session_correlation_id", None):
        g.session_correlation_id = uuid.uuid4().hex
    return g.session_correlation_id


def _error(response) -> RfSessionError:
    try:
        body = response.json() or {}
    except ValueError:
        body = {}
    details = body.get("details") or {}
    reason = details.get("reason")
    status = response.status_code
    if status >= 500:
        return SessionServiceUnavailable(f"HTTP {status}")
    if status == 401:
        return RfSessionError(N_("Your session expired. Sign in again."), 401)
    if status == 403:
        return RfSessionError(N_("Your role cannot perform this operation."), 403)
    message = REASON_MESSAGES.get(reason) or body.get("message") or N_("Unable to save this record right now.")
    # The RF screens answered 400 for every validation error (SessionService uses 422 for values).
    return RfSessionError(message, 400 if status == 422 else status)


def _request(method: str, path: str, *, json: Any = None, params: dict | None = None) -> Any:
    token = getattr(g, "auth_access_token", None)
    if not token:
        raise RfSessionError(N_("Your session expired. Sign in again."), 401)
    try:
        response = requests.request(
            method, f"{SESSION_SERVICE_URL}{BASE}{path}", json=json, params=params, timeout=SESSION_TIMEOUT,
            headers={"Authorization": f"Bearer {token}", "Accept": "application/json",
                     "X-Correlation-ID": _correlation_id()},
        )
    except requests.RequestException as exc:
        raise SessionServiceUnavailable(type(exc).__name__) from exc
    if response.status_code >= 400:
        raise _error(response)
    return response.json() if response.content else None


def health() -> dict:
    """GET /health (no token): used by the Supervisor's services page."""
    try:
        response = requests.get(f"{SESSION_SERVICE_URL}/health", timeout=SESSION_TIMEOUT)
        return {"ok": response.status_code == 200, "checks": (response.json() or {}).get("checks", {})}
    except (requests.RequestException, ValueError) as exc:
        return {"ok": False, "error": type(exc).__name__}


# --- Reads (snapshots) --------------------------------------------------------------------------


def list_sessions() -> list[dict]:
    return _request("GET", "", params={"expand": "detail"})


def session_detail(session_id) -> dict | None:
    try:
        return _request("GET", f"/{session_id}", params={"expand": "detail"})
    except RfSessionError as exc:
        if exc.status_code == 404:
            return None
        raise


def list_discrepancies() -> list[dict]:
    return _request("GET", "/discrepancies")


# --- Writes -------------------------------------------------------------------------------------


def _plan_body(*, operator_user_id, procedure_type_id, room_id, station_id, kit_id, patient_id, physicians,
               scheduled_at, expected_lines) -> dict:
    return {
        "operator_user_id": str(operator_user_id),
        "procedure_type_id": str(procedure_type_id),
        "room_id": str(room_id),
        "station_id": str(station_id),
        "kit_id": str(kit_id),
        "patient_id": str(patient_id) if patient_id else None,
        "scheduled_at": scheduled_at.isoformat(),
        "physicians": [{"physician_id": str(member["physician_id"]),
                        "surgical_role_code": member.get("surgical_role_code") or "surgeon"}
                       for member in physicians or []],
        "expected_lines": [{"family_id": str(line["family_id"]), "expected_quantity": int(line["expected_quantity"])}
                           for line in expected_lines],
    }


def schedule_session(*, phase_code, privacy_notice_version_id=None, purpose_model_improvement=False, **fields) -> dict:
    body = _plan_body(**fields)
    body["phase_code"] = phase_code
    if privacy_notice_version_id:
        body["privacy_notice_version_id"] = str(privacy_notice_version_id)
        body["purpose_model_improvement"] = bool(purpose_model_improvement)
    snapshot = _request("POST", "/schedule", json=body)
    return {"session_id": snapshot["id"]}


def update_scheduled_session(session_id, **fields) -> dict:
    _request("PUT", f"/{session_id}", json=_plan_body(**fields))
    return {"session_id": str(session_id)}


def confirm_privacy(session_id, *, privacy_notice_version_id, purpose_model_improvement) -> dict:
    _request("PUT", f"/{session_id}/privacy-agreement",
             json={"privacy_notice_version_id": str(privacy_notice_version_id),
                   "purpose_model_improvement": bool(purpose_model_improvement)})
    return {"session_id": str(session_id)}


def update_inventory(session_id, *, lines, physically_added) -> list[dict]:
    return _request("PUT", f"/{session_id}/expected-inventory", json={
        "lines": [{"family_id": str(line["family_id"]), "expected_quantity": int(line["expected_quantity"])}
                  for line in lines],
        "physically_added": bool(physically_added),
    })


def resolve_discrepancy(session_id, discrepancy_id, *, notes, outcome) -> dict:
    return _request("POST", f"/{session_id}/discrepancies/{discrepancy_id}/resolve",
                    json={"notes": notes, "outcome": outcome})


def close_session(session_id, *, material_recovered, contingency_reviewed, unreconciled_shortage_acknowledged=False,
                  unverified_contingency_acknowledged=False, escalation_acknowledged=False) -> dict:
    return _request("POST", f"/{session_id}/close", json={
        "material_recovered": bool(material_recovered), "contingency_reviewed": bool(contingency_reviewed),
        "unreconciled_shortage_acknowledged": bool(unreconciled_shortage_acknowledged),
        "unverified_contingency_acknowledged": bool(unverified_contingency_acknowledged),
        "escalation_acknowledged": bool(escalation_acknowledged)})


def operator_close(session_id, *, physical_integrity_confirmed, material_recovered) -> dict:
    """POST /sessions/{id}/operator-close with the Operator's own token (station_operator)."""
    return _request("POST", f"/{session_id}/operator-close", json={
        "physical_integrity_confirmed": bool(physical_integrity_confirmed),
        "material_recovered": bool(material_recovered)})


# --- Supervisor reports (complement v0.3.0, read-only aggregates) ---------------------------------


def report_summary(params: dict) -> dict:
    return _request("GET", "/reports/summary", params=params)


def report_kits(params: dict) -> dict:
    return _request("GET", "/reports/kits", params=params)


def report_operators(params: dict) -> dict:
    return _request("GET", "/reports/operators", params=params)


def report_stations(params: dict) -> dict:
    return _request("GET", "/reports/stations", params=params)


def tray_sessions(tray_id: str) -> dict:
    """MS-08: sessions that used a physical tray (Supervisor)."""
    return _request("GET", f"/trays/{tray_id}")


def tracking_events(session_id: str, offset: int = 0, limit: int = 100) -> dict:
    """MS-12: tracking log of the accepted final run (Supervisor; traceability, never a count)."""
    return _request("GET", f"/{session_id}/tracking-events", params={"offset": offset, "limit": limit})


def validation_seal(session_id: str) -> dict:
    """MS-12: verification of the integrity seal of the Operator's validation (intact / altered / not_sealed)."""
    return _request("GET", f"/{session_id}/validation-seal")


def report_export(params: dict) -> tuple[bytes, str, str]:
    """MS-06: the CSV / PDF file SessionService builds (and audits) for the same filters: (content, type, filename)."""
    token = getattr(g, "auth_access_token", None)
    if not token:
        raise RfSessionError(N_("Your session expired. Sign in again."), 401)
    try:
        response = requests.get(f"{SESSION_SERVICE_URL}{BASE}/reports/export", params=params, timeout=max(SESSION_TIMEOUT, 30),
                                headers={"Authorization": f"Bearer {token}", "X-Correlation-ID": _correlation_id()})
    except requests.RequestException as exc:
        raise SessionServiceUnavailable(type(exc).__name__) from exc
    if response.status_code >= 400:
        raise _error(response)
    disposition = response.headers.get("Content-Disposition", "")
    filename = disposition.split('filename="', 1)[1].split('"', 1)[0] if 'filename="' in disposition else ""
    # Only a plain file name is passed on to the browser's Content-Disposition (no quotes, paths or CR/LF).
    filename = "".join(ch for ch in filename if ch.isascii() and (ch.isalnum() or ch in "._-")).lstrip(".") or "pef-report"
    return response.content, response.headers.get("Content-Type", "application/octet-stream"), filename


def report_options() -> dict:
    return _request("GET", "/reports/options")
