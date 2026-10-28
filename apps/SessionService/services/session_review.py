"""SPD review use cases — Phase E/E.1 (PEF-DS09 §1.3.4 v0.1.7, PEF-DS04 v0.2.2 §1.3.7, PEF-DS03 §1.3.9.1).

- POST /sessions/{id}/discrepancies/{discrepancy_id}/resolve → resolve_discrepancy (spd_supervisor)
- POST /sessions/{id}/close                                  → close_session       (spd_supervisor)

Lock order (DS03 §1.3.9.1, same for every writer so resolve/close never deadlock):
work_session → discrepancy → instrument (by id) → instrument_reservation (by id). Each row is
locked alone (no FOR UPDATE over a join) and its state is re-read after the lock.

Physical piece (PEF-DS09 §1.3.4 complement v0.2.0, the portal's RF-SP-06 rule): with `instrument_id`,
`lost` / `recovered` act on exactly that piece (DS03 §1.3.9.1 checks below). Without it, the piece is
chosen from the discrepancy's family as the portal did: `lost` -> an active piece of the family (one
reserved for this operation first, then by id); `recovered` -> a lost piece of the family, and when
there is none the resolution notes say so. A discrepancy without family has no physical effect;
`resolved` never has one.

Resolution is allowed once the Operator finished (correction_required or awaiting_spd_review), never
on a closed session. The close needs material_recovered and, after a contingency count, the
contingency_reviewed confirmation. Business errors carry details.reason.

Physical shortage (reason `shortage`: the human-confirmed count is below the expected quantity or the
reference count; a YOLO classification error the human corrected never creates a discrepancy):
- `resolved` (a note) cannot close it: 409 physical_shortage_needs_piece_outcome. The Supervisor
  records `lost` (the piece leaves the stock; traceable cycle event) or `recovered`.
- Neither outcome documents that THIS missing instrument was physically recovered: the system records
  no piece-level identity of what entered the surgery and has no recount after the Operator's report.
  So a session with a physical shortage is never a safe close. Its close needs the explicit
  `unreconciled_shortage_acknowledged: true` (409 unreconciled_physical_shortage otherwise) and is
  recorded with safe_close_confirmed = false and the shortage ids; the incident stays traceable.

Transitions (DS03 §1.3.9.1):
- lost:      active piece in available/reserved/in_use → lost, active = false; releases the active
             reservation of THAT piece (operation of the same institution); 1 cycle event;
- recovered: lost → available, active = true; no reservation is created; 1 cycle event;
- close:     reserved pieces of the session's operation (same institution) that are active and in
             available/reserved/in_use → sterilization (1 cycle event each); every such reservation
             is released; other pieces keep their state. operation.status is never changed.
"""

from __future__ import annotations

import logging
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
from services.session_service import detail_item

logger = logging.getLogger("session.review")

RESOLVE_FIELDS = ("notes", "outcome", "instrument_id")
CLOSE_FIELDS = ("material_recovered", "contingency_reviewed", "unreconciled_shortage_acknowledged")
PHYSICAL_SHORTAGE = "shortage"
REVIEWABLE_STATES = ("correction_required", "awaiting_spd_review")
OUTCOME_LABELS = {"resolved": "resolved", "lost": "marked lost", "recovered": "marked recovered"}
OUTCOMES = ("resolved", "lost", "recovered")
OUTCOMES_NEEDING_PIECE = ("lost", "recovered")
# DS03 §1.3.9.1: origins from which Session moves an active piece (to lost, or to sterilization on close).
IN_SERVICE_STATES = ("available", "reserved", "in_use")
LOST, AVAILABLE, STERILIZATION = "lost", "available", "sterilization"
CYCLE_NOTES = {
    "lost": "SPD discrepancy resolution: lost",
    "recovered": "SPD discrepancy resolution: recovered",
    "close": "SPD close: reprocessing started",
}


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _uuid_or_404(raw: str, message: str) -> uuid.UUID:
    try:
        return uuid.UUID(str(raw))
    except (TypeError, ValueError):
        raise ApiError.not_found(message) from None


def _reject_unknown(payload: dict[str, Any], allowed: tuple[str, ...]) -> None:
    unknown = sorted(set(payload) - set(allowed))
    if unknown:
        raise ApiError(400, "Unknown fields in request body", details={"unknown_fields": unknown})


def validate_resolve(payload: dict[str, Any]) -> tuple[str, str, uuid.UUID | None]:
    _reject_unknown(payload, RESOLVE_FIELDS)   # also legacy flags such as lost/recovered booleans
    missing = [name for name in ("notes", "outcome") if payload.get(name) is None]
    if missing:
        raise ApiError(400, "Required fields are missing", details={"missing_fields": missing})
    notes, outcome, raw_instrument = payload["notes"], payload["outcome"], payload.get("instrument_id")
    if not isinstance(notes, str) or not notes.strip():
        raise ApiError(400, "notes must be a non-empty string", details={"field": "notes", "reason": "notes_required"})
    if outcome not in OUTCOMES:
        raise ApiError(400, "outcome must be resolved, lost or recovered",
                       details={"field": "outcome", "allowed": list(OUTCOMES)})
    if outcome not in OUTCOMES_NEEDING_PIECE:
        if raw_instrument is not None:
            raise ApiError(400, "instrument_id is only allowed with outcome lost or recovered",
                           details={"field": "instrument_id"})
        return notes.strip(), outcome, None
    if raw_instrument is None:
        return notes.strip(), outcome, None   # piece chosen from the discrepancy's family
    try:
        instrument_id = uuid.UUID(raw_instrument) if isinstance(raw_instrument, str) else None
    except ValueError:
        instrument_id = None
    if instrument_id is None:
        raise ApiError(400, "instrument_id must be a UUID string", details={"field": "instrument_id"})
    return notes.strip(), outcome, instrument_id


def _optional_bool(payload: dict[str, Any], name: str) -> bool:
    value = payload.get(name, False)
    if value is None:
        return False
    if not isinstance(value, bool):
        raise ApiError(400, f"{name} must be a boolean", details={"field": name})
    return value


def validate_close(payload: dict[str, Any]) -> tuple[bool, bool]:
    """Returns (contingency_reviewed, unreconciled_shortage_acknowledged)."""
    _reject_unknown(payload, CLOSE_FIELDS)
    if payload.get("material_recovered") is not True:
        raise ApiError(400, "material_recovered must be true",
                       details={"field": "material_recovered", "reason": "material_not_recovered"})
    return (_optional_bool(payload, "contingency_reviewed"),
            _optional_bool(payload, "unreconciled_shortage_acknowledged"))


class SessionReviewService:
    def __init__(self, database: Database):
        self.database = database

    @staticmethod
    def _locked_tenant_session(connection, principal: Principal, session_id: uuid.UUID) -> dict[str, Any]:
        session = repo.lock_session(connection, session_id)
        if session is None or session["institution_id"] != principal.institution_id:
            raise ApiError(404, "Session not found", details={"reason": "session_not_found"})
        return session

    # --- POST /sessions/{id}/discrepancies/{discrepancy_id}/resolve ---------------------------------

    def resolve_discrepancy(
        self, principal: Principal, raw_session_id: str, raw_discrepancy_id: str, payload: dict[str, Any],
        correlation_id: str | None,
    ) -> dict[str, Any]:
        session_id = _uuid_or_404(raw_session_id, "Session not found")
        discrepancy_id = _uuid_or_404(raw_discrepancy_id, "Discrepancy not found")
        notes, outcome, instrument_id = validate_resolve(payload)
        tenant = principal.institution_id
        now = _utc_now()
        with self.database.begin() as connection:
            session = self._locked_tenant_session(connection, principal, session_id)
            if session["status_code"] not in REVIEWABLE_STATES:
                raise ApiError.conflict("Discrepancies can only be resolved after the Operator finished and "
                                        "before the SPD close",
                                        details={"status_code": session["status_code"],
                                                 "reason": "session_not_reviewable"})
            found = repo.lock_discrepancy(connection, discrepancy_id, session_id)
            if found is None:   # unknown, or belongs to another session: same answer
                raise ApiError(404, "Discrepancy not found", details={"reason": "discrepancy_not_found"})
            if found["resolved"]:
                raise ApiError.conflict("Discrepancy is already resolved",
                                        details={"discrepancy_id": str(discrepancy_id), "reason": "already_resolved"})
            if outcome == "resolved" and repo.discrepancy_reason_code(connection, found["reason_id"]) == PHYSICAL_SHORTAGE:
                # A note is not evidence that a physically missing instrument was found.
                raise ApiError.conflict("A physically missing instrument cannot be resolved with a note: "
                                        "record the piece as lost (it remains an unreconciled incident)",
                                        details={"discrepancy_id": str(discrepancy_id),
                                                 "reason": "physical_shortage_needs_piece_outcome",
                                                 "allowed": ["lost", "recovered"]})

            description = f"{found['description']}\nSPD resolution ({OUTCOME_LABELS[outcome]}): {notes}"
            if outcome in OUTCOMES_NEEDING_PIECE and instrument_id is None and found["family_id"] is not None:
                instrument_id = self._piece_of_family(connection, session, found, outcome, tenant)
                if instrument_id is None and outcome == "recovered":
                    description += "\nNote: no lost piece of this family was found to reactivate."
                if instrument_id is None and outcome == "lost":
                    description += "\nNote: no piece of this family in service was found to mark lost."
            if instrument_id is not None:
                self._apply_piece_outcome(connection, session, found, outcome, instrument_id, tenant, now)

            # DS04 §1.3.7: no resolution-notes column; the untranslated text is appended to description.
            repo.update_discrepancy(connection, discrepancy_id, {
                "resolved": True,
                "resolved_at": now,
                "description": description,
                "updated_at": now,
            })
            open_remaining = repo.count_open_discrepancies(connection, session_id)
            status_code = session["status_code"]
            if open_remaining == 0 and status_code == "correction_required":
                status_code = "awaiting_spd_review"
                repo.update_session(connection, session_id, {
                    "status_id": repo.session_status_id(connection, status_code), "updated_at": now,
                })
            audit_adapter.record(
                connection, action=audit_adapter.ACTION_DISCREPANCY_RESOLVE, actor_user_id=principal.user_id,
                session_id=session_id, institution_id=tenant, correlation_id=correlation_id, occurred_at=now,
            )
        logger.info("discrepancy resolved session=%s discrepancy=%s outcome=%s open_remaining=%d",
                    session_id, discrepancy_id, outcome, open_remaining)
        return {
            "discrepancy_id": str(discrepancy_id),
            "session_id": str(session_id),
            "open_remaining": open_remaining,
            "session_status_code": status_code,
        }

    @staticmethod
    def _piece_of_family(connection, session: dict[str, Any], discrepancy: dict[str, Any], outcome: str,
                         tenant: uuid.UUID) -> uuid.UUID | None:
        """The portal's family rule (no instrument_id in the request)."""
        if outcome == "lost":
            # No piece of the family in service: the discrepancy is resolved without physical effect.
            return plan_repo.first_active_piece_of_family(connection, discrepancy["family_id"], tenant,
                                                          session["operation_id"])
        return plan_repo.first_lost_piece_of_family(connection, discrepancy["family_id"], tenant)

    @staticmethod
    def _apply_piece_outcome(connection, session: dict[str, Any], discrepancy: dict[str, Any], outcome: str,
                             instrument_id: uuid.UUID, tenant: uuid.UUID, now: datetime) -> None:
        """DS03 §1.3.9.1 lost / recovered on exactly the indicated piece (locked after session and discrepancy)."""
        piece = repo.lock_instruments(connection, [instrument_id]).get(instrument_id)
        # Unknown, other institution or other family than the discrepancy: one indistinguishable answer.
        if (piece is None or piece["institution_id"] != tenant or discrepancy["family_id"] is None
                or piece["family_id"] != discrepancy["family_id"]):
            raise ApiError.invalid_reference("Instrument is not valid for this discrepancy",
                                             details={"field": "instrument_id"})
        state = {"cycle_status_code": piece["cycle_status_code"], "active": piece["active"]}
        if outcome == "lost":
            if not (piece["active"] and piece["cycle_status_code"] in IN_SERVICE_STATES):
                raise ApiError.conflict("The instrument cannot be marked lost from its current state", details=state)
            target, active = LOST, False
        else:
            if piece["cycle_status_code"] != LOST:
                raise ApiError.conflict("Only a lost instrument can be recovered", details=state)
            target, active = AVAILABLE, True

        if outcome == "lost":
            reservation_ids = repo.active_reservation_ids_of_instrument(connection, instrument_id, tenant)
            for reservation in repo.lock_reservations(connection, reservation_ids):
                if reservation["active"] and reservation["instrument_id"] == instrument_id:   # re-check after lock
                    repo.release_reservation(connection, reservation["id"], now)
        statuses = repo.cycle_status_ids(connection)
        repo.update_instrument_cycle(connection, instrument_id, statuses[target], now, active=active)
        repo.insert_cycle_event(connection, {
            "instrument_id": instrument_id,
            "cycle_status_id": statuses[target],
            "occurred_at": now,
            "session_id": session["id"],
            "operation_id": session["operation_id"],
            "notes": CYCLE_NOTES[outcome],
        })

    # --- POST /sessions/{id}/close -----------------------------------------------------------------

    def close_session(
        self, principal: Principal, raw_session_id: str, payload: dict[str, Any], correlation_id: str | None
    ) -> dict[str, Any]:
        session_id = _uuid_or_404(raw_session_id, "Session not found")
        contingency_reviewed, shortage_acknowledged = validate_close(payload)
        tenant = principal.institution_id
        now = _utc_now()
        with self.database.begin() as connection:
            session = self._locked_tenant_session(connection, principal, session_id)
            if session["status_code"] != "awaiting_spd_review":
                raise ApiError.conflict("Session is not awaiting SPD review",
                                        details={"status_code": session["status_code"],
                                                 "reason": "not_awaiting_spd_review"})
            open_discrepancies = repo.count_open_discrepancies(connection, session_id)
            if open_discrepancies:
                raise ApiError.conflict("Resolve open discrepancies before closing",
                                        details={"open_discrepancies": open_discrepancies,
                                                 "reason": "open_discrepancies"})
            contingency = plan_repo.has_contingency(connection, session_id)
            if contingency and not contingency_reviewed:
                raise ApiError(400, "Confirm that you reviewed the contingency count before closing",
                               details={"field": "contingency_reviewed", "reason": "contingency_review_required"})
            shortages = repo.physical_shortage_ids(connection, session_id)
            if shortages and not shortage_acknowledged:
                raise ApiError.conflict("The session has a physically missing instrument that the system cannot "
                                        "document as recovered: it can only be closed as an unreconciled shortage",
                                        details={"reason": "unreconciled_physical_shortage",
                                                 "field": "unreconciled_shortage_acknowledged",
                                                 "discrepancy_ids": [str(d) for d in shortages]})
            safe_close = not shortages

            released, sterilized = self._release_operation_pieces(connection, session, tenant, now)

            values: dict[str, Any] = {
                "status_id": repo.session_status_id(connection, "closed"),
                "closed_by_user_id": principal.user_id,
                "updated_at": now,
            }
            if session["ended_at"] is None:   # DS09: ended_at only "si faltaba"
                values["ended_at"] = now
            repo.update_session(connection, session_id, values)
            repo.insert_count_event(connection, {
                "event_type_id": repo.event_type_id(connection, "session_close"),
                "session_id": session_id,
                "user_id": principal.user_id,
                "occurred_at": now,
                "payload": {"closed_by": "spd_supervisor", "material_recovered": True, "safe_close_confirmed": safe_close,
                            "contingency_reviewed": contingency, "released_reservations": released,
                            "sterilized_instruments": sterilized,
                            "unreconciled_physical_shortages": [str(d) for d in shortages]},
            })
            audit_adapter.record(
                connection, action=audit_adapter.ACTION_SPD_CLOSE, actor_user_id=principal.user_id,
                session_id=session_id, institution_id=tenant, correlation_id=correlation_id, occurred_at=now,
            )
            detail = detail_item(read_repo.get_session(connection, session_id, tenant, None))
        logger.info("session closed session=%s safe_close=%s unreconciled_shortages=%d released=%d sterilized=%d",
                    session_id, safe_close, len(shortages), released, sterilized)
        return detail   # safe_close_confirmed + shortage ids: in the session_close count event

    @staticmethod
    def _release_operation_pieces(connection, session: dict[str, Any], tenant: uuid.UUID,
                                  now: datetime, cycle_note: str = CYCLE_NOTES["close"]) -> tuple[int, int]:
        """DS03 §1.3.9.1 close side effect, scoped to this operation AND to pieces of this institution.

        Shared by the SPD close and the Operator close (session_operator_close)."""
        operation_id = session["operation_id"]
        candidates = repo.scoped_active_reservations(connection, operation_id, tenant)
        # Lock order: instruments, then reservations (both by id), then re-check what was read before waiting.
        pieces = repo.lock_instruments(connection, sorted({row["instrument_id"] for row in candidates}))
        reservations = [
            row for row in repo.lock_reservations(connection, [row["id"] for row in candidates])
            if row["active"] and row["operation_id"] == operation_id
        ]
        statuses = repo.cycle_status_ids(connection)
        released = sterilized = 0
        for reservation in reservations:
            piece = pieces.get(reservation["instrument_id"])
            if piece is None or piece["institution_id"] != tenant:
                continue   # never touch another institution's stock
            repo.release_reservation(connection, reservation["id"], now)
            released += 1
            if piece["active"] and piece["cycle_status_code"] in IN_SERVICE_STATES:
                repo.update_instrument_cycle(connection, piece["id"], statuses[STERILIZATION], now)
                repo.insert_cycle_event(connection, {
                    "instrument_id": piece["id"],
                    "cycle_status_id": statuses[STERILIZATION],
                    "occurred_at": now,
                    "session_id": session["id"],
                    "operation_id": operation_id,
                    "notes": cycle_note,
                })
                sterilized += 1
        return released, sterilized
