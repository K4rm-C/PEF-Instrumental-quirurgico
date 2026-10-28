"""Supervisor planning use cases (PEF-DS09 §1.3.4 complement v0.2.0) — the RF-SP-02 rules of the portal.

- POST /sessions/schedule               → schedule        (operation + patient + team + session + inventory)
- PUT  /sessions/{id}                   → update_scheduled (full edit while scheduled)
- PUT  /sessions/{id}/privacy-agreement → confirm_privacy  (DS07 agreement, RF-SP-02 Via A)

Same validations, order and outcomes as apps/BackendWebFlask/services/rf_session.py
(schedule_session, update_scheduled_session, confirm_privacy_via_a), which these replace for the
Supervisor; every error carries details.reason (the portal shows its own localized message for it).
One transaction per call, with its access_audit rows (session.scheduled, session.scheduled_updated,
privacy.session_agreement). The edit and the agreement lock the work_session row first, the same
row the Operator's transactions lock in the portal, so a session that started meanwhile answers 409.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from adapters import audit_adapter
from auth import OPERATOR_ROLE_CODE, Principal
from database import Database
from errors import ApiError
from repositories import supervisor_repository as plan_repo
from repositories import write_repository as repo
from services.session_snapshot import SessionSnapshotService

SMALLINT_MAX = 32767
SCHEDULE_FIELDS = ("operator_user_id", "procedure_type_id", "room_id", "station_id", "kit_id", "patient_id",
                   "scheduled_at", "phase_code", "physicians", "expected_lines", "privacy_notice_version_id",
                   "purpose_model_improvement")
UPDATE_FIELDS = ("operator_user_id", "procedure_type_id", "room_id", "station_id", "kit_id", "patient_id",
                 "scheduled_at", "physicians", "expected_lines")
PRIVACY_FIELDS = ("privacy_notice_version_id", "purpose_model_improvement")


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def business_error(status: int, reason: str, message: str, **details: Any) -> ApiError:
    error = {400: "invalid_request", 404: "not_found", 409: "conflict", 422: "validation_error"}[status]
    return ApiError(status, message, error=error, details={"reason": reason, **details})


def _reject_unknown(payload: dict[str, Any], allowed: tuple[str, ...]) -> None:
    unknown = sorted(set(payload) - set(allowed))
    if unknown:
        raise ApiError(400, "Unknown fields in request body", details={"unknown_fields": unknown})


def _uuid(payload: dict[str, Any], name: str, *, required: bool = True) -> uuid.UUID | None:
    value = payload.get(name)
    if value in (None, "") and not required:
        return None
    try:
        return uuid.UUID(str(value)) if isinstance(value, str) else None
    except ValueError:
        return None


def _required_uuid(payload: dict[str, Any], name: str, reason: str, message: str) -> uuid.UUID:
    value = _uuid(payload, name)
    if value is None:
        raise business_error(422, reason, message, field=name)
    return value


def _scheduled_at(payload: dict[str, Any]) -> datetime:
    raw = payload.get("scheduled_at")
    try:
        value = datetime.fromisoformat(str(raw).replace("Z", "+00:00")) if isinstance(raw, str) and raw else None
    except ValueError:
        value = None
    if value is None:
        raise business_error(422, "invalid_scheduled_at", "scheduled_at must be an ISO-8601 date-time",
                             field="scheduled_at")
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _lines(payload: dict[str, Any], field: str) -> list[tuple[uuid.UUID, int]]:
    raw = payload.get(field)
    if not isinstance(raw, list) or not raw:
        raise business_error(400, "lines_required", "Provide at least one inventory line", field=field)
    lines: list[tuple[uuid.UUID, int]] = []
    seen: set[uuid.UUID] = set()
    for item in raw:
        if not isinstance(item, dict):
            raise ApiError(400, "Each line must be an object", details={"field": field})
        _reject_unknown(item, ("family_id", "expected_quantity"))
        family_id = _uuid(item, "family_id")
        quantity = item.get("expected_quantity")
        if isinstance(quantity, str) and quantity.strip().lstrip("-").isdigit():
            quantity = int(quantity.strip())
        if family_id is None or isinstance(quantity, bool) or not isinstance(quantity, int):
            raise ApiError(400, "Each line needs family_id (UUID) and an integer expected_quantity",
                           details={"field": field})
        if quantity < 1 or quantity > SMALLINT_MAX:
            raise business_error(422, "invalid_quantity", "Expected quantities must be >= 1",
                                 family_id=str(family_id))
        if family_id in seen:
            raise ApiError(400, "Duplicate family in lines", details={"family_id": str(family_id)})
        seen.add(family_id)
        lines.append((family_id, quantity))
    return lines


def check_stock(connection, lines: list[tuple[uuid.UUID, int]], institution_id: uuid.UUID) -> None:
    stock = repo.available_stock(connection, [family_id for family_id, _ in lines], institution_id)
    for family_id, quantity in lines:
        if quantity > stock.get(family_id, 0):
            raise business_error(422, "stock_block", "Requested quantity exceeds available instruments",
                                 family_id=str(family_id))


def _kit_source(family_id: uuid.UUID, quantity: int, kit_qty: dict[uuid.UUID, int]) -> str:
    return "kit_snapshot" if kit_qty.get(family_id) == quantity else "schedule_additional"


class SessionPlanningService:
    def __init__(self, database: Database, snapshots: SessionSnapshotService):
        self.database = database
        self.snapshots = snapshots

    # --- Shared validation (rf_session._validate_* and _normalize_physicians) --------------------

    @staticmethod
    def _references(connection, payload: dict[str, Any], tenant: uuid.UUID) -> dict[str, Any]:
        operator_id = _required_uuid(payload, "operator_user_id", "invalid_operator", "Choose a valid operator")
        operator = plan_repo.get_user(connection, operator_id)
        if operator is None or not operator["active"] or operator["institution_id"] != tenant:
            raise business_error(422, "invalid_operator", "Choose a valid operator", field="operator_user_id")
        if not plan_repo.has_effective_role(connection, operator_id, tenant, OPERATOR_ROLE_CODE):
            raise business_error(422, "operator_role_required", "Assigned user must have station_operator role",
                                 field="operator_user_id")

        room_id = _required_uuid(payload, "room_id", "invalid_room", "Choose a valid operating room")
        room = plan_repo.get_room(connection, room_id)
        if room is None or room["institution_id"] != tenant:
            raise business_error(422, "invalid_room", "Choose a valid operating room", field="room_id")
        station_id = _required_uuid(payload, "station_id", "invalid_station",
                                    "Choose a capture station in the selected operating room")
        station = plan_repo.get_station(connection, station_id)
        if station is None or station["room_id"] != room_id:
            raise business_error(422, "invalid_station", "Choose a capture station in the selected operating room",
                                 field="station_id")

        kit_id = _required_uuid(payload, "kit_id", "invalid_kit", "Choose a valid kit")
        kit = plan_repo.get_kit(connection, kit_id)
        if kit is None or kit["institution_id"] != tenant:
            raise business_error(422, "invalid_kit", "Choose a valid kit", field="kit_id")

        patient_id = None
        if payload.get("patient_id") not in (None, ""):
            patient_id = _uuid(payload, "patient_id")
            patient = plan_repo.get_patient(connection, patient_id) if patient_id else None
            if patient is None or patient["institution_id"] != tenant:
                raise business_error(422, "invalid_patient", "Choose a valid patient", field="patient_id")

        procedure_id = _required_uuid(payload, "procedure_type_id", "invalid_procedure", "Choose a valid procedure")
        if not plan_repo.procedure_type_exists(connection, procedure_id):
            raise business_error(422, "invalid_procedure", "Choose a valid procedure", field="procedure_type_id")

        team = SessionPlanningService._team(connection, payload.get("physicians"), tenant)
        return {"operator_id": operator_id, "room_id": room_id, "station_id": station_id, "kit_id": kit_id,
                "patient_id": patient_id, "procedure_id": procedure_id, "team": team}

    @staticmethod
    def _team(connection, raw: Any, tenant: uuid.UUID) -> list[tuple[uuid.UUID, uuid.UUID]]:
        if raw in (None, []):
            raise business_error(400, "physicians_required", "At least one physician is required", field="physicians")
        if not isinstance(raw, list):
            raise ApiError(400, "physicians must be a list", details={"field": "physicians"})
        team: list[tuple[uuid.UUID, uuid.UUID]] = []
        seen: set[uuid.UUID] = set()
        for entry in raw:
            if not isinstance(entry, dict):
                raise ApiError(400, "Each physician row must be an object", details={"field": "physicians"})
            _reject_unknown(entry, ("physician_id", "surgical_role_code"))
            if entry.get("physician_id") in (None, ""):
                raise business_error(400, "physician_required", "Each surgical team row needs a physician",
                                     field="physicians")
            physician_id = _uuid(entry, "physician_id")
            if physician_id is not None and physician_id in seen:
                raise business_error(400, "duplicate_physician", "Each physician can only be selected once",
                                     field="physicians")
            physician = plan_repo.get_physician(connection, physician_id) if physician_id else None
            if physician is None or not physician["active"] or physician["institution_id"] != tenant:
                raise business_error(422, "invalid_physician", "Choose a valid physician", field="physicians")
            seen.add(physician_id)
            role_code = (entry.get("surgical_role_code") or "surgeon").strip() or "surgeon"
            role_id = plan_repo.surgical_role_id(connection, role_code)
            if role_id is None:
                raise business_error(422, "invalid_surgical_role", f"Invalid surgical role: {role_code}",
                                     field="physicians", surgical_role_code=role_code)
            team.append((physician_id, role_id))
        return team

    # --- POST /sessions/schedule ------------------------------------------------------------------

    def schedule(self, principal: Principal, payload: dict[str, Any], correlation_id: str | None) -> dict[str, Any]:
        _reject_unknown(payload, SCHEDULE_FIELDS)
        tenant = principal.institution_id
        now = _utc_now()
        scheduled_at = _scheduled_at(payload)
        lines = _lines(payload, "expected_lines")
        notice_id = None
        if payload.get("privacy_notice_version_id") not in (None, ""):
            notice_id = _required_uuid(payload, "privacy_notice_version_id", "invalid_privacy_notice",
                                       "Choose an active privacy notice version")
        model_improvement = payload.get("purpose_model_improvement")
        if model_improvement is not None and not isinstance(model_improvement, bool):
            raise ApiError(400, "purpose_model_improvement must be a boolean",
                           details={"field": "purpose_model_improvement"})
        with self.database.begin() as connection:
            refs = self._references(connection, payload, tenant)
            check_stock(connection, lines, tenant)
            phase_code = payload.get("phase_code")
            phase_id = plan_repo.phase_id_by_code(connection, phase_code) if isinstance(phase_code, str) else None
            if phase_id is None:
                raise business_error(422, "invalid_phase", "Invalid initial phase", field="phase_code")
            notice = None
            if notice_id is not None:
                notice = plan_repo.privacy_notice(connection, notice_id)
                if notice is None or not notice["active"]:
                    raise business_error(422, "invalid_privacy_notice", "Choose an active privacy notice version",
                                         field="privacy_notice_version_id")

            operation_id, session_id = uuid.uuid4(), uuid.uuid4()
            plan_repo.insert_operation(connection, {
                "id": operation_id, "scheduled_at": scheduled_at,
                "status_id": plan_repo.operation_status_id(connection, "scheduled"),
                "procedure_type_id": refs["procedure_id"], "room_id": refs["room_id"], "institution_id": tenant,
            })
            plan_repo.replace_operation_people(connection, operation_id, refs["patient_id"], refs["team"])
            repo.insert_session(connection, {
                "id": session_id, "status_id": repo.session_status_id(connection, "scheduled"),
                "user_id": refs["operator_id"], "operation_id": operation_id, "station_id": refs["station_id"],
                "kit_id": refs["kit_id"], "current_phase_id": phase_id, "capture_mode": None, "started_at": None,
                "ended_at": None, "closed_by_user_id": None, "updated_at": now,
            })
            kit_qty = repo.kit_quantities(connection, refs["kit_id"])
            for family_id, quantity in lines:
                repo.insert_expected_line(connection, session_id, family_id, quantity,
                                          _kit_source(family_id, quantity, kit_qty))
            audit_adapter.record(connection, action=audit_adapter.ACTION_SESSION_SCHEDULED,
                                 actor_user_id=principal.user_id, session_id=session_id, institution_id=tenant,
                                 correlation_id=correlation_id, occurred_at=now)
            if notice is not None:
                plan_repo.upsert_agreement(connection, session_id, notice_id, bool(model_improvement),
                                           principal.user_id, now)
                audit_adapter.record(connection, action=audit_adapter.ACTION_PRIVACY_AGREEMENT,
                                     actor_user_id=principal.user_id, session_id=session_id, institution_id=tenant,
                                     correlation_id=correlation_id, occurred_at=now)
        return self.snapshots.detail(principal, str(session_id))

    # --- PUT /sessions/{id} -------------------------------------------------------------------------

    def update_scheduled(self, principal: Principal, raw_session_id: str, payload: dict[str, Any],
                         correlation_id: str | None) -> dict[str, Any]:
        session_id = _session_id(raw_session_id)
        _reject_unknown(payload, UPDATE_FIELDS)
        tenant = principal.institution_id
        now = _utc_now()
        scheduled_at = _scheduled_at(payload)
        lines = _lines(payload, "expected_lines")
        with self.database.begin() as connection:
            session = _locked(connection, session_id, tenant)
            if session["status_code"] != "scheduled":
                raise business_error(409, "not_scheduled", "Full session edit is only available while scheduled",
                                     status_code=session["status_code"])
            refs = self._references(connection, payload, tenant)
            check_stock(connection, lines, tenant)
            plan_repo.lock_operation_row(connection, session["operation_id"])
            plan_repo.update_operation(connection, session["operation_id"], scheduled_at=scheduled_at,
                                       procedure_type_id=refs["procedure_id"], room_id=refs["room_id"])
            plan_repo.update_session_assignment(connection, session_id, user_id=refs["operator_id"],
                                                station_id=refs["station_id"], kit_id=refs["kit_id"], now=now)
            plan_repo.replace_operation_people(connection, session["operation_id"], refs["patient_id"], refs["team"])
            kit_qty = repo.kit_quantities(connection, refs["kit_id"])
            existing = repo.expected_rows(connection, session_id)
            submitted = set()
            for family_id, quantity in lines:
                submitted.add(family_id)
                source = _kit_source(family_id, quantity, kit_qty)
                if family_id in existing:
                    repo.update_expected_line(connection, existing[family_id]["id"], quantity, source)
                else:
                    repo.insert_expected_line(connection, session_id, family_id, quantity, source)
            repo.delete_expected_lines(connection, [row["id"] for fid, row in existing.items() if fid not in submitted])
            audit_adapter.record(connection, action=audit_adapter.ACTION_SCHEDULED_UPDATED,
                                 actor_user_id=principal.user_id, session_id=session_id, institution_id=tenant,
                                 correlation_id=correlation_id, occurred_at=now)
        return self.snapshots.detail(principal, str(session_id))

    # --- PUT /sessions/{id}/privacy-agreement ------------------------------------------------------

    def confirm_privacy(self, principal: Principal, raw_session_id: str, payload: dict[str, Any],
                        correlation_id: str | None) -> dict[str, Any]:
        session_id = _session_id(raw_session_id)
        _reject_unknown(payload, PRIVACY_FIELDS)
        notice_id = _required_uuid(payload, "privacy_notice_version_id", "invalid_privacy_notice",
                                   "Choose an active privacy notice version")
        model_improvement = payload.get("purpose_model_improvement", False)
        if model_improvement is None:
            model_improvement = False
        if not isinstance(model_improvement, bool):
            raise ApiError(400, "purpose_model_improvement must be a boolean",
                           details={"field": "purpose_model_improvement"})
        tenant = principal.institution_id
        now = _utc_now()
        with self.database.begin() as connection:
            session = _locked(connection, session_id, tenant)
            if session["status_code"] != "scheduled":
                raise business_error(409, "privacy_not_scheduled",
                                     "Privacy agreement can only be set while session is scheduled",
                                     status_code=session["status_code"])
            if session["capture_mode"] is not None:
                raise business_error(409, "capture_mode_frozen",
                                     "Capture mode already frozen; cannot change privacy agreement")
            notice = plan_repo.privacy_notice(connection, notice_id)
            if notice is None or not notice["active"]:
                raise business_error(422, "invalid_privacy_notice", "Choose an active privacy notice version",
                                     field="privacy_notice_version_id")
            plan_repo.upsert_agreement(connection, session_id, notice_id, model_improvement, principal.user_id, now)
            audit_adapter.record(connection, action=audit_adapter.ACTION_PRIVACY_AGREEMENT,
                                 actor_user_id=principal.user_id, session_id=session_id, institution_id=tenant,
                                 correlation_id=correlation_id, occurred_at=now)
        return self.snapshots.detail(principal, str(session_id))


def _session_id(raw: str) -> uuid.UUID:
    try:
        return uuid.UUID(str(raw))
    except (TypeError, ValueError):
        raise business_error(404, "session_not_found", "Session not found") from None


def _locked(connection, session_id: uuid.UUID, tenant: uuid.UUID) -> dict[str, Any]:
    session = repo.lock_session(connection, session_id)
    if session is None or session["institution_id"] != tenant:
        raise business_error(404, "session_not_found", "Session not found")
    return session
