"""Session write use cases — Phase C (PEF-DS09 §1.3.4, PEF-DS04 v0.2.1).

- POST /sessions                     → create_session   (spd_supervisor)
- PUT  /sessions/{id}/expected-inventory → replace_expected_inventory (spd_supervisor)
- POST /sessions/{id}/start          → start_session    (assigned station_operator)

Every operation is ONE PostgreSQL transaction: domain rows + its access_audit row (audit adapter)
commit together or not at all. Rows that decide the outcome are locked first (SELECT ... FOR UPDATE):
the operation for create, the work_session for inventory/start; state is evaluated after the lock,
so a concurrent writer that already changed it gets 409.

Session never calls VisionWorker: start only *decides* capture_mode (DS07 agreement) and reports it.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from adapters import audit_adapter
from auth import OPERATOR_ROLE_CODE, Principal
from database import Database
from errors import ApiError
from repositories import session_repository as read_repo
from repositories import supervisor_repository as plan_repo
from repositories import write_repository as repo

SMALLINT_MAX = 32767
EDITABLE_INVENTORY_STATES = ("scheduled", "in_progress")
CREATE_FIELDS = ("operation_id", "user_id", "station_id", "kit_id", "phase_code", "expected_lines")
LINE_FIELDS = ("family_id", "expected_quantity")
START_PHASE_CODE = "start"   # DS09 start: "fase inicial si faltaba"


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


# --- Request validation (shape and types; references are checked inside the transaction) ----


def _reject_unknown(payload: dict[str, Any], allowed: tuple[str, ...]) -> None:
    unknown = sorted(set(payload) - set(allowed))
    if unknown:
        raise ApiError(400, "Unknown fields in request body", details={"unknown_fields": unknown})


def _uuid_field(payload: dict[str, Any], name: str) -> uuid.UUID:
    value = payload.get(name)
    if not isinstance(value, str):
        raise ApiError(400, "Field must be a UUID string", details={"field": name})
    try:
        return uuid.UUID(value)
    except ValueError:
        raise ApiError(400, "Field must be a UUID string", details={"field": name}) from None


def validate_lines(raw: Any, field: str) -> list[tuple[uuid.UUID, int]]:
    """Non-empty list of {family_id, expected_quantity}; unique families; 1 ≤ quantity ≤ SMALLINT."""
    if not isinstance(raw, list) or not raw:
        raise ApiError(400, "At least one inventory line is required", details={"field": field, "reason": "lines_required"})
    lines: list[tuple[uuid.UUID, int]] = []
    seen: set[uuid.UUID] = set()
    for item in raw:
        if not isinstance(item, dict):
            raise ApiError(400, "Each line must be an object", details={"field": field})
        _reject_unknown(item, LINE_FIELDS)
        missing = [name for name in LINE_FIELDS if item.get(name) is None]
        if missing:
            raise ApiError(400, "Required fields are missing", details={"field": field, "missing_fields": missing})
        family_id = _uuid_field(item, "family_id")
        quantity = item["expected_quantity"]
        if isinstance(quantity, bool) or not isinstance(quantity, int):
            raise ApiError(400, "expected_quantity must be an integer", details={"family_id": str(family_id)})
        if quantity < 1 or quantity > SMALLINT_MAX:
            raise ApiError.validation_error(
                "expected_quantity out of range",
                details={"family_id": str(family_id), "min": 1, "max": SMALLINT_MAX, "reason": "invalid_quantity"},
            )
        if family_id in seen:
            raise ApiError(400, "Duplicate family in lines", details={"family_id": str(family_id)})
        seen.add(family_id)
        lines.append((family_id, quantity))
    return lines


def validate_create(payload: dict[str, Any]) -> dict[str, Any]:
    _reject_unknown(payload, CREATE_FIELDS)
    missing = [name for name in CREATE_FIELDS if payload.get(name) is None]
    if missing:
        raise ApiError(400, "Required fields are missing", details={"missing_fields": missing})
    phase_code = payload["phase_code"]
    if not isinstance(phase_code, str) or not phase_code.strip():
        raise ApiError(400, "phase_code must be a non-empty string", details={"field": "phase_code"})
    return {
        "operation_id": _uuid_field(payload, "operation_id"),
        "user_id": _uuid_field(payload, "user_id"),
        "station_id": _uuid_field(payload, "station_id"),
        "kit_id": _uuid_field(payload, "kit_id"),
        "phase_code": phase_code.strip(),
        "lines": validate_lines(payload["expected_lines"], "expected_lines"),
    }


def validate_inventory_update(payload: dict[str, Any]) -> tuple[list[tuple[uuid.UUID, int]], bool]:
    """(lines, physically_added). physically_added: the SPD Supervisor confirms the increases are
    instruments really added to the tray (only meaningful while in_progress)."""
    _reject_unknown(payload, ("lines", "physically_added"))
    physically_added = payload.get("physically_added", False)
    if physically_added is None:
        physically_added = False
    if not isinstance(physically_added, bool):
        raise ApiError(400, "physically_added must be a boolean", details={"field": "physically_added"})
    return validate_lines(payload.get("lines"), "lines"), physically_added


def validate_start(payload: dict[str, Any]) -> None:
    """DS09: start has no parameters; the server decides everything."""
    _reject_unknown(payload, ())


def _invalid_reference(field: str) -> ApiError:
    # Same message whether the row does not exist, is inactive or belongs to another tenant.
    return ApiError.invalid_reference("Referenced resource is not valid for this institution", details={"field": field})


def _kit_source(family_id: uuid.UUID, quantity: int, kit_qty: dict[uuid.UUID, int]) -> str:
    """DS04 §1.3.5: kit_snapshot only when the line equals the kit's quantity for that family."""
    return "kit_snapshot" if kit_qty.get(family_id) == quantity else "schedule_additional"


class SessionWriteService:
    def __init__(self, database: Database):
        self.database = database

    # --- Shared validation inside the transaction -----------------------------------------

    @staticmethod
    def _check_families_and_stock(connection, lines: list[tuple[uuid.UUID, int]], institution_id: uuid.UUID) -> None:
        family_ids = [family_id for family_id, _ in lines]
        valid = repo.active_family_ids(connection, family_ids)
        for family_id in family_ids:
            if family_id not in valid:
                raise ApiError.invalid_reference(
                    "Instrument family is not valid", details={"field": "family_id", "family_id": str(family_id)}
                )
        stock = repo.available_stock(connection, family_ids, institution_id)
        for family_id, quantity in lines:
            if quantity > stock.get(family_id, 0):
                raise ApiError.validation_error(
                    "Requested quantity exceeds available stock",
                    details={"family_id": str(family_id), "reason": "stock_block"},
                )

    @staticmethod
    def _detail(connection, session_id: uuid.UUID, institution_id: uuid.UUID) -> dict[str, Any]:
        from services.session_service import detail_item

        return detail_item(read_repo.get_session(connection, session_id, institution_id, None))

    # --- POST /sessions ---------------------------------------------------------------------

    def create_session(self, principal: Principal, payload: dict[str, Any], correlation_id: str | None) -> dict[str, Any]:
        data = validate_create(payload)
        tenant = principal.institution_id
        now = _utc_now()
        with self.database.begin() as connection:
            op = repo.lock_operation(connection, data["operation_id"])
            if op is None or op.get("institution_id") != tenant:
                raise _invalid_reference("operation_id")
            if op["status_code"] != "scheduled":
                raise ApiError.conflict("Operation is not in a state that allows scheduling a session",
                                        details={"field": "operation_id"})

            operator = read_repo.get_user_auth_state(connection, data["user_id"])
            if (operator is None or not operator["active"] or operator["institution_id"] != tenant
                    or OPERATOR_ROLE_CODE not in read_repo.list_role_codes(connection, data["user_id"], tenant)):
                raise _invalid_reference("user_id")

            station = repo.get_station(connection, data["station_id"])
            if (station is None or not station["active"] or station["room_institution_id"] != tenant
                    or (op["room_id"] is not None and station["room_id"] != op["room_id"])):
                raise _invalid_reference("station_id")

            kit = repo.get_kit(connection, data["kit_id"])
            if kit is None or not kit["active"] or kit["institution_id"] != tenant:
                raise _invalid_reference("kit_id")

            phase = repo.get_phase(connection, data["phase_code"])
            if phase is None or not phase["active"]:
                raise _invalid_reference("phase_code")

            self._check_families_and_stock(connection, data["lines"], tenant)
            kit_qty = repo.kit_quantities(connection, data["kit_id"])

            session_id = uuid.uuid4()
            repo.insert_session(connection, {
                "id": session_id,
                "status_id": repo.session_status_id(connection, "scheduled"),
                "user_id": data["user_id"],
                "operation_id": data["operation_id"],
                "station_id": data["station_id"],
                "kit_id": data["kit_id"],
                "current_phase_id": phase["id"],
                "capture_mode": None,
                "started_at": None,
                "ended_at": None,
                "closed_by_user_id": None,
                "updated_at": now,
            })
            for family_id, quantity in data["lines"]:
                repo.insert_expected_line(connection, session_id, family_id, quantity,
                                          _kit_source(family_id, quantity, kit_qty))
            audit_adapter.record(
                connection, action=audit_adapter.ACTION_SESSION_SCHEDULED, actor_user_id=principal.user_id,
                session_id=session_id, institution_id=tenant, correlation_id=correlation_id, occurred_at=now,
            )
            return self._detail(connection, session_id, tenant)

    # --- PUT /sessions/{id}/expected-inventory -------------------------------------------------

    def replace_expected_inventory(
        self, principal: Principal, raw_session_id: str, payload: dict[str, Any], correlation_id: str | None
    ) -> list[dict[str, Any]]:
        session_id = _parse_session_id(raw_session_id)
        lines, physically_added = validate_inventory_update(payload)
        tenant = principal.institution_id
        now = _utc_now()
        with self.database.begin() as connection:
            # Same row the Operator's report locks in the portal: an edit and a report never interleave.
            session = repo.lock_session(connection, session_id)
            if session is None or session["institution_id"] != tenant:
                raise ApiError(404, "Session not found", details={"reason": "session_not_found"})
            status = session["status_code"]
            if status not in EDITABLE_INVENTORY_STATES:
                raise ApiError.conflict("Expected inventory can only change while scheduled or in progress",
                                        details={"status_code": status, "reason": "inventory_locked"})
            self._check_families_and_stock(connection, lines, tenant)

            existing = repo.expected_rows(connection, session_id)
            kit_qty = repo.kit_quantities(connection, session["kit_id"])
            previous_total = sum(int(row["expected_quantity"]) for row in existing.values())
            # Only increases over the stored quantity count: resubmitting the same lines adds nothing.
            additions = []
            for family_id, quantity in lines:
                before = int(existing[family_id]["expected_quantity"]) if family_id in existing else 0
                if quantity > before:
                    additions.append({"family_id": str(family_id), "previous": before, "new": quantity,
                                      "quantity": quantity - before})
            submitted = set()
            for family_id, quantity in lines:
                submitted.add(family_id)
                current = existing.get(family_id)
                if current is not None and int(current["expected_quantity"]) == quantity:
                    continue  # unchanged line keeps its provenance
                source = "live_add" if status == "in_progress" else _kit_source(family_id, quantity, kit_qty)
                if current is None:
                    repo.insert_expected_line(connection, session_id, family_id, quantity, source)
                else:
                    repo.update_expected_line(connection, current["id"], quantity, source)
            repo.delete_expected_lines(
                connection, [row["id"] for family_id, row in existing.items() if family_id not in submitted]
            )
            repo.update_session(connection, session_id, {"updated_at": now})
            added_total = sum(item["quantity"] for item in additions)
            confirmed = physically_added and status == "in_progress" and added_total > 0
            if confirmed:
                # DS04 instruments_added: raises the vision count reference (initial + confirmed additions).
                reference = plan_repo.count_reference(connection, session_id)
                repo.insert_count_event(connection, {
                    "event_type_id": repo.event_type_id(connection, "instruments_added"),
                    "session_id": session_id,
                    "user_id": principal.user_id,
                    "family_id": None,
                    "expected_quantity": previous_total,
                    "detected_quantity": added_total,
                    "occurred_at": now,
                    "payload": {"added": additions, "confirmed_by_role": "spd_supervisor",
                                "reference_before": reference["total"] if reference else None},
                })
            audit_adapter.record(
                connection,
                action=audit_adapter.ACTION_INSTRUMENTS_ADDED if confirmed else audit_adapter.ACTION_INVENTORY_UPDATED,
                actor_user_id=principal.user_id, session_id=session_id, institution_id=tenant,
                correlation_id=correlation_id, occurred_at=now,
            )
            rows = read_repo.list_expected_inventory(connection, session_id)
        return [
            {"family_id": str(row["family_id"]), "family_code": row["family_code"],
             "expected_quantity": int(row["expected_quantity"]), "source": row["source"]}
            for row in rows
        ]

    # --- POST /sessions/{id}/start ---------------------------------------------------------------

    def start_session(
        self, principal: Principal, raw_session_id: str, payload: dict[str, Any], correlation_id: str | None
    ) -> dict[str, Any]:
        session_id = _parse_session_id(raw_session_id)
        validate_start(payload)
        tenant = principal.institution_id
        now = _utc_now()
        with self.database.begin() as connection:
            session = repo.lock_session(connection, session_id)
            # Other tenant, unknown, or assigned to another operator: indistinguishable 404.
            if session is None or session["institution_id"] != tenant or session["user_id"] != principal.user_id:
                raise ApiError.not_found("Session not found")
            if session["status_code"] != "scheduled":
                raise ApiError.conflict("Session is not scheduled", details={"status_code": session["status_code"]})
            if session["capture_mode"] is not None:
                # Inconsistent data: never overwrite a frozen capture_mode silently.
                raise ApiError.conflict("Session capture mode is already set", details={"status_code": "scheduled"})

            capture_mode = "vision" if repo.has_valid_agreement(connection, session_id, now) else "manual_no_privacy"

            values: dict[str, Any] = {
                "status_id": repo.session_status_id(connection, "in_progress"),
                "started_at": now,
                "capture_mode": capture_mode,
                "updated_at": now,
            }
            phase_id = session["current_phase_id"]
            if phase_id is None:
                start_phase = repo.get_phase(connection, START_PHASE_CODE)
                if start_phase is not None and start_phase["active"]:
                    phase_id = start_phase["id"]
                    values.update(current_phase_id=phase_id, phase_changed_at=now)

            # DS09: copy the kit lines only when the session has no expected inventory (legacy rows).
            if not repo.expected_rows(connection, session_id):
                for family_id, quantity in repo.kit_quantities(connection, session["kit_id"]).items():
                    repo.insert_expected_line(connection, session_id, family_id, quantity, "kit_snapshot")

            repo.update_session(connection, session_id, values)
            phase = repo.get_phase_by_id(connection, phase_id) if phase_id else None
            repo.insert_count_event(connection, {
                "event_type_id": repo.event_type_id(connection, "session_open"),
                "session_id": session_id,
                "user_id": principal.user_id,
                "occurred_at": now,
                "payload": {"phase_code": phase["code"] if phase else None, "capture_mode": capture_mode},
            })
            audit_adapter.record(
                connection, action=audit_adapter.ACTION_SESSION_START, actor_user_id=principal.user_id,
                session_id=session_id, institution_id=tenant, correlation_id=correlation_id, occurred_at=now,
            )
            return self._detail(connection, session_id, tenant)


def _parse_session_id(raw: str) -> uuid.UUID:
    try:
        return uuid.UUID(str(raw))
    except (TypeError, ValueError):
        raise ApiError.not_found("Session not found") from None
