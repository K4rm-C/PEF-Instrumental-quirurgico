"""Operator close of a reconciled vision session — POST /sessions/{id}/operator-close (station_operator).

The assigned Operator closes the session directly (no SPD approval) when the counts are reconciled and
nothing was ever raised. Same lock order and close side effects as the SPD close (session_review):
work_session → instrument → instrument_reservation; reserved pieces of the operation go to
sterilization and every such reservation is released (DS03 §1.3.9.1).

Every condition is evaluated here, under the work_session row lock (the portal's screen is never
trusted). The Operator close is allowed only when ALL hold:
- session in awaiting_spd_review, capture_mode vision, assigned to the caller (another Operator or
  institution → 404, as for the reads);
- the Operator finished through YOLO human validation (manual_close operator_close_path =
  vision_validation): no contingency count, no instruments outside the kit confirmed, no YOLO
  difference carried into the report (known_final_mismatch);
- no discrepancy at all (open or resolved) and no rejected final count (close_blocked);
- initial count from the FIRST frame and final count from the LAST frame of the same video (SHA-256),
  the final count newer than the last confirmed addition and equal to the reference total
  (initial + confirmed additions); neither lot holds detections outside the kit;
- the reference total equals the instruments required by the expected inventory, and the validated
  quantity of every family equals its expected quantity (so their sum equals the final count);
- body: physical_integrity_confirmed = true and material_recovered = true.

Anything else stays with the SPD Supervisor (409 operator_close_not_allowed, details.blockers). The
close is recorded with closed_by = station_operator and safe_close_confirmed = true — only reachable
without any discrepancy, so never with a physical shortage. A repeated request after the same
Operator closed the session answers the session again without a second close (idempotent).
"""

from __future__ import annotations

import logging
import hashlib
import uuid
from datetime import datetime, timezone
from typing import Any

from adapters import audit_adapter
from auth import Principal
from database import Database
from errors import ApiError
from repositories import session_repository as read_repo
from repositories import supervisor_repository as plan_repo
from repositories import write_repository as repo
from services.session_review import SessionReviewService, _optional_bool, _reject_unknown, _uuid_or_404
from services.session_service import detail_item

logger = logging.getLogger("session.operator_close")

FIELDS = ("physical_integrity_confirmed", "material_recovered")
CYCLE_NOTE = "Operator close: reprocessing started"


def validate_operator_close(payload: dict[str, Any]) -> None:
    _reject_unknown(payload, FIELDS)
    if _optional_bool(payload, "physical_integrity_confirmed") is not True:
        raise ApiError(400, "physical_integrity_confirmed must be true",
                       details={"field": "physical_integrity_confirmed", "reason": "physical_integrity_not_confirmed"})
    if _optional_bool(payload, "material_recovered") is not True:
        raise ApiError(400, "material_recovered must be true",
                       details={"field": "material_recovered", "reason": "material_not_recovered"})


def _payload(event: dict[str, Any] | None) -> dict[str, Any]:
    return event["payload"] if event and isinstance(event.get("payload"), dict) else {}


def eligibility(connection, session: dict[str, Any]) -> dict[str, Any]:
    """Blocker codes (empty = the Operator may close) and the reconciliation summary."""
    session_id = session["id"]
    blockers: list[str] = []
    if session["status_code"] != "awaiting_spd_review":
        blockers.append("not_awaiting_review")
    if session["capture_mode"] != "vision":
        blockers.append("not_vision")
    closes = plan_repo.events_of_type(connection, session_id, "manual_close")
    report = _payload(closes[-1] if closes else None)
    if report.get("operator_close_path") != "vision_validation":
        blockers.append("not_vision_validation")
    if report.get("contingency"):
        blockers.append("contingency")
    if report.get("extra_instruments"):
        blockers.append("extra_instruments")
    if report.get("known_final_mismatch"):
        blockers.append("final_count_mismatch")
    discrepancies = repo.count_discrepancies(connection, session_id)
    if discrepancies:
        blockers.append("discrepancies_recorded")
    if plan_repo.events_of_type(connection, session_id, "close_blocked"):
        blockers.append("count_blocked")

    lots = [event for event in plan_repo.events_of_type(connection, session_id, "auto_count", board_only=True)
            if _payload(event).get("trigger") in ("initial", "final")]
    initial = next((event for event in lots if _payload(event).get("trigger") == "initial"), None)
    finals = [event for event in lots if _payload(event).get("trigger") == "final"]
    final = finals[-1] if finals else None
    for event in (initial, final):
        payload = _payload(event)
        image = payload.get("frame_jpeg_b64")
        if not isinstance(image, str) or not image or hashlib.sha256(image.encode("utf-8")).hexdigest() != payload.get("frame_sha256"):
            blockers.append("evidence_integrity")
    initial_sha = (_payload(initial).get("video") or {}).get("sha256")
    if initial is None or _payload(initial).get("source") != "first_frame" or not initial_sha:
        blockers.append("missing_initial")
    additions = [event for event in plan_repo.events_of_type(connection, session_id, "instruments_added")
                 if initial is not None and event["occurred_at"] > initial["occurred_at"]]
    changed_at = additions[-1]["occurred_at"] if additions else (initial["occurred_at"] if initial else None)
    initial_total = int(initial["detected_quantity"] or 0) if initial else None
    added_total = sum(int(event["detected_quantity"] or 0) for event in additions)
    reference_total = initial_total + added_total if initial else None
    final_total = int(final["detected_quantity"] or 0) if final else None
    if final is None or _payload(final).get("source") != "last_frame" or changed_at is None \
            or final["occurred_at"] <= changed_at:
        blockers.append("missing_final")
    elif (_payload(final).get("video") or {}).get("sha256") != initial_sha:
        blockers.append("different_video")
    if final is not None and reference_total is not None and final_total != reference_total:
        blockers.append("initial_final_mismatch")
    if any(_payload(event).get("out_of_kit") for event in (initial, final) if event is not None):
        blockers.append("out_of_kit_detected")

    expected = {row["family_id"]: int(row["expected_quantity"])
                for row in plan_repo.expected_inventory_detail(connection, session_id)}
    human = {}
    for event in plan_repo.events_of_type(connection, session_id, "manual_count"):
        if event["family_id"] is not None:
            human[event["family_id"]] = int(event["detected_quantity"] or 0)
    expected_total = sum(expected.values())
    validated_total = sum(human.get(family_id, 0) for family_id in expected)
    initial_families = {str(row.get("family_id")): int(row.get("detected_quantity") or 0)
                        for row in (_payload(initial).get("board") or {}).get("families", [])}
    if initial_families != {str(family_id): qty for family_id, qty in expected.items()}:
        blockers.append("initial_family_mismatch")
    if any(qty and family_id not in expected for family_id, qty in human.items()):
        blockers.append("extra_instruments")
    if not expected or any(human.get(family_id) != qty for family_id, qty in expected.items()):
        blockers.append("family_mismatch")
    if reference_total is not None and expected_total != reference_total:
        blockers.append("expected_total_mismatch")
    if final_total is not None and validated_total != final_total:
        blockers.append("validated_total_mismatch")
    return {
        "blockers": list(dict.fromkeys(blockers)),
        "summary": {"expected_total": expected_total, "initial_total": initial_total, "added_total": added_total,
                    "reference_total": reference_total, "final_total": final_total,
                    "validated_total": validated_total, "discrepancies": discrepancies},
    }


class SessionOperatorCloseService:
    def __init__(self, database: Database):
        self.database = database

    def close(self, principal: Principal, raw_session_id: str, payload: dict[str, Any],
              correlation_id: str | None) -> dict[str, Any]:
        session_id = _uuid_or_404(raw_session_id, "Session not found")
        validate_operator_close(payload)
        tenant = principal.institution_id
        now = datetime.now(timezone.utc)
        with self.database.begin() as connection:
            session = repo.lock_session(connection, session_id)
            # Another institution or another Operator's session: same answer as the reads (not found).
            if session is None or session["institution_id"] != tenant or session["user_id"] != principal.user_id:
                raise ApiError(404, "Session not found", details={"reason": "session_not_found"})
            if session["status_code"] == "closed":
                closes = plan_repo.events_of_type(connection, session_id, "session_close")
                if _payload(closes[-1] if closes else None).get("closed_by") == "station_operator" and \
                        closes[-1]["user_id"] == principal.user_id:
                    return detail_item(read_repo.get_session(connection, session_id, tenant, None))   # idempotent
                raise ApiError.conflict("Session is already closed", details={"reason": "already_closed"})
            result = eligibility(connection, session)
            if result["blockers"]:
                raise ApiError.conflict("This session does not meet the conditions for an Operator close; it stays "
                                        "in Supervisor review",
                                        details={"reason": "operator_close_not_allowed",
                                                 "blockers": result["blockers"]})

            released, sterilized = SessionReviewService._release_operation_pieces(connection, session, tenant, now,
                                                                                 cycle_note=CYCLE_NOTE)
            values: dict[str, Any] = {
                "status_id": repo.session_status_id(connection, "closed"),
                "closed_by_user_id": principal.user_id,
                "updated_at": now,
            }
            if session["ended_at"] is None:
                values["ended_at"] = now
            repo.update_session(connection, session_id, values)
            repo.insert_count_event(connection, {
                "event_type_id": repo.event_type_id(connection, "session_close"),
                "session_id": session_id,
                "user_id": principal.user_id,
                "occurred_at": now,
                "payload": {"closed_by": "station_operator", "operator_close": True, "material_recovered": True,
                            "physical_integrity_confirmed": True, "safe_close_confirmed": True,
                            "contingency_reviewed": False, "released_reservations": released,
                            "sterilized_instruments": sterilized, "unreconciled_physical_shortages": [],
                            "reconciliation": result["summary"]},
            })
            audit_adapter.record(
                connection, action=audit_adapter.ACTION_OPERATOR_CLOSE, actor_user_id=principal.user_id,
                session_id=session_id, institution_id=tenant, correlation_id=correlation_id, occurred_at=now,
            )
            detail = detail_item(read_repo.get_session(connection, session_id, tenant, None))
        logger.info("session closed by operator session=%s released=%d sterilized=%d", session_id, released, sterilized)
        return detail
