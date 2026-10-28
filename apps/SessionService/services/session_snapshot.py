"""Session snapshot reads for the Supervisor (PEF-DS09 §1.3.4 complement v0.2.0, `expand=detail`).

The representation is the one the portal's RF screens are built from; the portal also produces it
locally for the Operator screens (apps/BackendWebFlask/services/session_snapshot.py). Both must give
the same result for the same session: same fields, order, ISO-8601 UTC dates and the same payload
projection (only the count_event payload keys the screens use, never the whole document).
tests/test_snapshot_parity.py checks it against the portal's loader on real rows.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from auth import SUPERVISOR_ROLE_CODE, Principal
from database import Database
from errors import ApiError
from repositories import supervisor_repository as repo

# count_event.payload keys the RF screens read (timeline, AI / human quantities, contingency).
PAYLOAD_SCALAR_KEYS = ("reason", "scope", "trigger", "reported_quantity", "frames_processed", "video_t", "pipeline",
                       "solver", "model_version", "verified_total")
PAYLOAD_OBJECT_KEYS = ("contingency", "reference", "known_final_mismatch")
STATUS_CODES = ("scheduled", "in_progress", "awaiting_spd_review", "correction_required", "closed", "aborted")


def iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _str(value: Any) -> str | None:
    return None if value is None else str(value)


def project_payload(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {}
    result: dict[str, Any] = {key: payload[key] for key in PAYLOAD_SCALAR_KEYS if key in payload}
    for key in PAYLOAD_OBJECT_KEYS:
        if isinstance(payload.get(key), dict):
            result[key] = payload[key]
    board = payload.get("board")
    if isinstance(board, dict):
        totals = board.get("totals") if isinstance(board.get("totals"), dict) else {}
        families = board.get("families") if isinstance(board.get("families"), list) else []
        result["board"] = {
            "totals": {"family_count": totals.get("family_count")} if "family_count" in totals else {},
            "families": [{"family_id": item.get("family_id"), "detected_quantity": item.get("detected_quantity")}
                         for item in families if isinstance(item, dict)],
        }
    config = payload.get("pipeline_config")
    if isinstance(config, dict) and "solver" in config:
        result["pipeline_config"] = {"solver": config["solver"]}
    return result


def summary_item(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(row["id"]),
        "status": {"code": row["status_code"], "name": row["status_name"]},
        "capture_mode": row["capture_mode"],
        "operator": {"id": str(row["user_id"]), "name": row["operator_name"]} if row["user_id"] else None,
        "operation": {
            "id": str(row["operation_id"]),
            "scheduled_at": iso(row["scheduled_at"]),
            "institution_id": str(row["institution_id"]),
            "procedure_type": ({"id": str(row["procedure_id"]), "code": row["procedure_code"], "name": row["procedure_name"]}
                               if row["procedure_id"] else None),
            "room": ({"id": str(row["room_id"]), "code": row["room_code"], "name": row["room_name"]}
                     if row["room_id"] else None),
        },
        "kit": {"id": str(row["kit_id"]), "name": row["kit_name"]} if row["kit_id"] and row["kit_name"] is not None else None,
        "station": ({"id": str(row["station_id"]), "name": row["station_name"]}
                    if row["station_id"] and row["station_name"] is not None else None),
        "started_at": iso(row["started_at"]),
        "ended_at": iso(row["ended_at"]),
        "updated_at": iso(row["updated_at"]),
        "closed_by_user_id": _str(row["closed_by_user_id"]),
        "open_discrepancies": int(row["open_discrepancies"] or 0),
        "has_privacy_agreement": bool(row["has_agreement"]),
    }


def discrepancy_item(row: dict[str, Any], *, with_reason_name: bool = False) -> dict[str, Any]:
    item = {
        "id": str(row["id"]),
        "session_id": str(row["session_id"]),
        "description": row["description"],
        "resolved": bool(row["resolved"]),
        "resolved_at": iso(row["resolved_at"]),
        "expected_quantity": row["expected_quantity"],
        "detected_quantity": row["detected_quantity"],
        "family": ({"id": str(row["family_id"]), "code": row["family_code"], "name": row["family_name"]}
                   if row["family_id"] else None),
        "reason_code": row["reason_code"],
        "origin_event_code": row["origin_event_code"],
    }
    if with_reason_name:
        item["reason_name"] = row["reason_name"]
    return item


def _status_filter(raw: str | None) -> list[str] | None:
    if not raw:
        return None
    codes = [code.strip() for code in raw.split(",") if code.strip()]
    unknown = sorted(set(codes) - set(STATUS_CODES))
    if unknown:
        raise ApiError.invalid_request("Unknown session status", details={"field": "status", "allowed": list(STATUS_CODES)})
    return codes


class SessionSnapshotService:
    def __init__(self, database: Database):
        self.database = database

    @staticmethod
    def _operator_filter(principal: Principal) -> uuid.UUID | None:
        # Same scope as the DS09 reads: a supervisor sees the tenant, an operator only their sessions.
        return None if principal.has_role(SUPERVISOR_ROLE_CODE) else principal.user_id

    def list_summaries(self, principal: Principal, status: str | None = None) -> list[dict[str, Any]]:
        codes = _status_filter(status)
        with self.database.connect() as connection:
            rows = repo.list_session_summaries(connection, principal.institution_id,
                                               operator_user_id=self._operator_filter(principal), status_codes=codes)
        return [summary_item(row) for row in rows]

    def detail(self, principal: Principal, raw_session_id: str) -> dict[str, Any]:
        try:
            session_id = uuid.UUID(str(raw_session_id))
        except (TypeError, ValueError):
            raise ApiError.not_found("Session not found") from None
        with self.database.connect() as connection:
            row = repo.get_session_summary(connection, session_id, principal.institution_id,
                                           self._operator_filter(principal))
            if row is None:
                raise ApiError.not_found("Session not found")
            snapshot = summary_item(row)
            phase = repo.get_phase(connection, row["current_phase_id"])
            snapshot["phase"] = {"id": str(phase["id"]), "code": phase["code"], "name": phase["name"]} if phase else None
            snapshot["expected_inventory"] = [{
                "family_id": str(line["family_id"]),
                "family_code": line["family_code"],
                "family_name": line["family_name"],
                "category": ({"id": str(line["category_id"]), "code": line["category_code"], "name": line["category_name"]}
                             if line["category_id"] else None),
                "expected_quantity": int(line["expected_quantity"]),
                "source": line["source"],
            } for line in repo.expected_inventory_detail(connection, session_id)]
            snapshot["events"] = [{
                "id": str(event["id"]),
                "occurred_at": iso(event["occurred_at"]),
                "event_code": event["event_code"],
                "event_name": event["event_name"],
                "user_id": _str(event["user_id"]),
                "family_id": _str(event["family_id"]),
                "expected_quantity": event["expected_quantity"],
                "detected_quantity": event["detected_quantity"],
                "payload": project_payload(event["payload"]),
            } for event in repo.count_events_detail(connection, session_id)]
            snapshot["discrepancies"] = [discrepancy_item(item) for item in repo.session_discrepancies(connection, session_id)]
            agreement = repo.privacy_agreement(connection, session_id)
            snapshot["privacy"] = {
                "version": agreement["version"],
                "document_uri": agreement["document_uri"],
                "agreed_at": iso(agreement["agreed_at"]),
                "purpose_quality_ops": bool(agreement["purpose_quality_ops"]),
                "purpose_model_improvement": bool(agreement["purpose_model_improvement"]),
                "agreed_by": ({"id": str(agreement["agreed_by_user_id"]), "name": agreement["agreed_by_name"]}
                              if agreement["agreed_by_user_id"] else None),
            } if agreement else None
            patient = repo.operation_patient(connection, row["operation_id"])
            snapshot["patient"] = ({"id": str(patient["patient_id"]), "display_name": patient["display_name"]}
                                   if patient else None)
            snapshot["surgical_team"] = [{"physician_id": str(member["physician_id"]), "name": member["name"],
                                          "role_code": member["role_code"], "role_name": member["role_name"]}
                                         for member in repo.surgical_team(connection, row["operation_id"])]
            snapshot["contingency"] = self._contingency(connection, snapshot["events"])
        return snapshot

    @staticmethod
    def _contingency(connection, events: list[dict[str, Any]]) -> dict[str, Any] | None:
        closes = [event for event in events if event["event_code"] == "manual_close"]
        if not closes or not closes[-1]["payload"].get("contingency"):
            return None
        last = closes[-1]
        payload = last["payload"]
        user_id = last["user_id"]
        return {**payload["contingency"], "event_id": last["id"], "occurred_at": last["occurred_at"],
                "user_id": user_id, "user_name": repo.user_name(connection, uuid.UUID(user_id)) if user_id else None,
                "verified_total": payload.get("verified_total"), "reference": payload.get("reference"),
                "known_final_mismatch": payload.get("known_final_mismatch")}

    def discrepancies(self, principal: Principal, status: str | None = None) -> list[dict[str, Any]]:
        if status not in (None, "", "open", "resolved"):
            raise ApiError.invalid_request("status must be open or resolved", details={"field": "status"})
        resolved = None if not status else status == "resolved"
        with self.database.connect() as connection:
            rows = repo.tenant_discrepancies(connection, principal.institution_id, resolved)
        return [discrepancy_item(row, with_reason_name=True) for row in rows]
