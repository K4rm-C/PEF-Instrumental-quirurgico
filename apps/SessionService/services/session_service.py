"""Session read use cases (PEF-DS09 §1.3.4, PEF-DS04 v0.2.1).

Access rules:
- tenant = authenticated user's institution; tenant(session) = operation.institution_id;
- spd_supervisor (also when combined with station_operator): every session of the tenant;
- station_operator only: sessions where work_session.user_id = authenticated user;
- anything outside that scope, an invalid UUID or a session without operation → 404
  (same answer, so existence is never revealed). Sub-resources are loaded only after the
  session itself has been authorized.

Output is plain JSON-compatible values with the exact DS09 field set and order.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from auth import SUPERVISOR_ROLE_CODE, Principal
from database import Database
from errors import ApiError
from repositories import session_repository as repo

VISION_TRIGGERS = ("stable_window", "final")
SMALLINT_MAX = 32767


def _str(value: Any) -> str | None:
    return None if value is None else str(value)


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _quantity(value: Any) -> int | None:
    """A JSON quantity is accepted only if it is a real integer in the SMALLINT ≥ 0 range."""
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value if 0 <= value <= SMALLINT_MAX else None


def _uuid_str(value: Any) -> str | None:
    try:
        return str(uuid.UUID(str(value)))
    except (TypeError, ValueError):
        return None


# --- Legacy-safe extraction from count_event.payload (read-only compatibility) ------------


def _legacy_reported_quantity(payload: dict[str, Any]) -> int | None:
    """manual_count rows written by the legacy portal keep the human quantity in the payload."""
    return _quantity(payload.get("reported_quantity"))


def _vision_counts(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Normalized {family_id, detected_quantity} pairs of an auto_count batch.

    Current format: payload.counts[]. Legacy portal format: payload.board.families[].
    Only those two fields are extracted; boxes, scores, run/frame/video metadata are dropped.
    """
    entries = payload.get("counts")
    if not isinstance(entries, list):
        board = payload.get("board")
        entries = board.get("families") if isinstance(board, dict) else None
    if not isinstance(entries, list):
        return []
    result = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        family_id = _uuid_str(entry.get("family_id"))
        quantity = _quantity(entry.get("detected_quantity"))
        if family_id is not None and quantity is not None:
            result.append({"family_id": family_id, "detected_quantity": quantity})
    result.sort(key=lambda item: item["family_id"])
    return result


def _short_string(value: Any, max_length: int = 64) -> str | None:
    return value if isinstance(value, str) and 0 < len(value) <= max_length else None


def detail_item(row: dict[str, Any]) -> dict[str, Any]:
    """DS09 Session detail (also the response of POST /sessions and POST /start)."""
    return {
        "id": str(row["id"]),
        "operation_id": str(row["operation_id"]),
        "user_id": str(row["user_id"]),
        "station_id": _str(row["station_id"]),
        "kit_id": _str(row["kit_id"]),
        "status_code": row["status_code"],
        "capture_mode": row["capture_mode"],
        "current_phase_code": row["current_phase_code"],
        "scheduled_at": _iso(row["scheduled_at"]),
        "started_at": _iso(row["started_at"]),
        "ended_at": _iso(row["ended_at"]),
        "closed_by_user_id": _str(row["closed_by_user_id"]),
        "open_discrepancies": int(row["open_discrepancies"] or 0),
        "privacy_agreement": bool(row["privacy_agreement"]),
    }


class SessionService:
    def __init__(self, database: Database):
        self.database = database

    # --- Scope ----------------------------------------------------------------------

    @staticmethod
    def _scope(principal: Principal) -> tuple[uuid.UUID, uuid.UUID | None]:
        """(tenant, operator filter). Supervisor scope wins for multi-role users."""
        if principal.has_role(SUPERVISOR_ROLE_CODE):
            return principal.institution_id, None
        return principal.institution_id, principal.user_id

    def _authorized_session(self, connection, principal: Principal, raw_session_id: str) -> dict[str, Any]:
        try:
            session_id = uuid.UUID(str(raw_session_id))
        except (TypeError, ValueError):
            raise ApiError.not_found("Session not found") from None
        institution_id, operator_user_id = self._scope(principal)
        row = repo.get_session(connection, session_id, institution_id, operator_user_id)
        if row is None:
            raise ApiError.not_found("Session not found")
        return row

    # --- Endpoints ------------------------------------------------------------------

    def list_sessions(self, principal: Principal) -> list[dict[str, Any]]:
        institution_id, operator_user_id = self._scope(principal)
        with self.database.connect() as connection:
            rows = repo.list_sessions(connection, institution_id, operator_user_id)
        return [
            {
                "id": str(row["id"]),
                "status_code": row["status_code"],
                "capture_mode": row["capture_mode"],
                "scheduled_at": _iso(row["scheduled_at"]),
                "started_at": _iso(row["started_at"]),
                "ended_at": _iso(row["ended_at"]),
                "user_id": str(row["user_id"]),
                "kit_id": _str(row["kit_id"]),
                "open_discrepancies": int(row["open_discrepancies"] or 0),
            }
            for row in rows
        ]

    def get_session(self, principal: Principal, raw_session_id: str) -> dict[str, Any]:
        with self.database.connect() as connection:
            row = self._authorized_session(connection, principal, raw_session_id)
        return detail_item(row)

    def get_expected_inventory(self, principal: Principal, raw_session_id: str) -> list[dict[str, Any]]:
        with self.database.connect() as connection:
            session = self._authorized_session(connection, principal, raw_session_id)
            rows = repo.list_expected_inventory(connection, session["id"])
        return [
            {
                "family_id": str(row["family_id"]),
                "family_code": row["family_code"],
                "expected_quantity": int(row["expected_quantity"]),
                "source": row["source"],
            }
            for row in rows
        ]

    def get_counts(self, principal: Principal, raw_session_id: str) -> list[dict[str, Any]]:
        with self.database.connect() as connection:
            session = self._authorized_session(connection, principal, raw_session_id)
            rows = repo.list_count_events(connection, session["id"])
            reason_codes = repo.list_discrepancy_reason_codes(connection)
        return [self._count_item(row, reason_codes) for row in rows]

    @staticmethod
    def _count_item(row: dict[str, Any], reason_codes: set[str]) -> dict[str, Any]:
        payload = row["payload"] if isinstance(row["payload"], dict) else {}
        event_type = row["event_type_code"]
        detected = row["detected_quantity"]
        reason_code = None
        model_version = trigger = None
        counts: list[dict[str, Any]] = []

        if event_type == "manual_count":
            if detected is None:
                detected = _legacy_reported_quantity(payload)
            stored_reason = payload.get("reason_code")
            # Only catalog codes are exposed (the legacy UI value `other` is not one; DS04 §1.3.3).
            reason_code = stored_reason if isinstance(stored_reason, str) and stored_reason in reason_codes else None
        elif event_type == "auto_count":
            model_version = _short_string(payload.get("model_version"))
            stored_trigger = payload.get("trigger")
            trigger = stored_trigger if stored_trigger in VISION_TRIGGERS else None
            counts = _vision_counts(payload)

        human_correction = None
        if row["hc_id"] is not None:
            human_correction = {
                "justification": row["hc_justification"],
                "user_id": _str(row["hc_user_id"]),
                "recorded_at": _iso(row["hc_recorded_at"]),
            }

        return {
            "id": str(row["id"]),
            "event_type_code": event_type,
            "occurred_at": _iso(row["occurred_at"]),
            "user_id": _str(row["user_id"]),
            "client_event_id": _str(row["client_event_id"]),
            "family_id": _str(row["family_id"]),
            "family_code": row["family_code"],
            "expected_quantity": row["expected_quantity"],
            "detected_quantity": detected,
            "reason_code": reason_code,
            "model_version": model_version,
            "trigger": trigger,
            "counts": counts,
            "human_correction": human_correction,
        }

    def get_discrepancies(self, principal: Principal, raw_session_id: str) -> list[dict[str, Any]]:
        with self.database.connect() as connection:
            session = self._authorized_session(connection, principal, raw_session_id)
            rows = repo.list_discrepancies(connection, session["id"])
        return [
            {
                "id": str(row["id"]),
                "reason_code": row["reason_code"],
                "family_id": _str(row["family_id"]),
                "family_code": row["family_code"],
                "expected_quantity": row["expected_quantity"],
                "detected_quantity": row["detected_quantity"],
                "resolved": bool(row["resolved"]),
                "resolved_at": _iso(row["resolved_at"]),
                "origin_event_id": _str(row["origin_event_id"]),
                "description": row["description"],
            }
            for row in rows
        ]
