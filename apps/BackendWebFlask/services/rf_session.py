"""RF session write paths: Start (OP-03), manual close (OP-06M), SPD schedule/privacy (SP-02)."""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError

from extensions import db
from flask_babel import gettext

# N_ marks user-facing RfSessionError messages (routes translate them); localize_* give the
# display labels of system-controlled DB values. Ids/codes returned for forms never change.
from i18n import N_, localize_db_label, localize_db_text
from models.AccessAudit import AccessAudit
from models.CaptureStation import CaptureStation
from models.CatDiscrepancyReason import CatDiscrepancyReason
from models.CatEventType import CatEventType
from models.CatInstrumentCategory import CatInstrumentCategory
from models.CatInstrumentCycleStatus import CatInstrumentCycleStatus
from models.CatOperationPhase import CatOperationPhase
from models.CatOperationStatus import CatOperationStatus
from models.CatProcedureType import CatProcedureType
from models.CatSessionStatus import CatSessionStatus
from models.CatSurgicalRole import CatSurgicalRole
from models.CountEvent import CountEvent
from models.Discrepancy import Discrepancy
from models.ExpectedInventory import ExpectedInventory
from models.HumanCorrection import HumanCorrection
from models.Instrument import Instrument
from models.InstrumentCycleEvent import InstrumentCycleEvent
from models.InstrumentFamily import InstrumentFamily
from models.InstrumentReservation import InstrumentReservation
from models.Kit import Kit
from models.KitItem import KitItem
from models.OperatingRoom import OperatingRoom
from models.Operation import Operation
from models.OperationPatient import OperationPatient
from models.OperationPhysician import OperationPhysician
from models.Patient import Patient
from models.Physician import Physician
from models.PrivacyNoticeVersion import PrivacyNoticeVersion
from models.Role import Role
from models.SessionProcessingAgreement import SessionProcessingAgreement
from models.User import User
from models.UserRole import UserRole
from models.WorkSession import WorkSession


EXTRA_AUTO_ACK_ON_MANUAL_CLOSE = False
MANUAL_REPORT_QTY_DEFAULT = "expected"

EXPECTED_SOURCE_LABELS = {
    "kit_snapshot": "Kit",
    "schedule_additional": "Additional (schedule)",
    "live_add": "Added during session",
    "manual": "Manual",
}


class RfSessionError(Exception):
    def __init__(self, message: str, status_code: int = 400, **params):
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        # %(name)s values for the translated message (routes pass them to gettext).
        self.params = params


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _status_id(code: str) -> UUID:
    row = db.session.scalar(select(CatSessionStatus).where(CatSessionStatus.code == code))
    if row is None:
        raise RfSessionError(f"Missing session status catalog code: {code}", 500)
    return row.id


def _operation_status_id(code: str) -> UUID:
    row = db.session.scalar(select(CatOperationStatus).where(CatOperationStatus.code == code))
    if row is None:
        raise RfSessionError(f"Missing operation status catalog code: {code}", 500)
    return row.id


def _event_type_id(code: str) -> UUID:
    row = db.session.scalar(select(CatEventType).where(CatEventType.code == code))
    if row is None:
        raise RfSessionError(f"Missing event type catalog code: {code}", 500)
    return row.id


def _discrepancy_reason_id(code: str) -> UUID | None:
    row = db.session.scalar(select(CatDiscrepancyReason).where(CatDiscrepancyReason.code == code))
    return row.id if row else None


def _audit(*, user_id: UUID, action: str, resource_id: UUID, institution_id: UUID | None, ip: str | None = None):
    db.session.add(AccessAudit(
        occurred_at=utc_now(),
        actor_type="user",
        actor_user_id=user_id,
        action=action,
        resource_type="work_session",
        resource_id=resource_id,
        institution_id=institution_id,
        outcome="success",
        ip=ip,
    ))


# --- Tenant guards ------------------------------------------------------------------------
# tenant(session) = operation.institution_id. The caller's tenant is the authenticated user's
# institution_id. Anything outside it is reported as "not found" (never which tenant owns it).


def _session_tenant_id(work: WorkSession) -> UUID | None:
    if work is None or work.operation_id is None:
        return None
    operation = db.session.get(Operation, work.operation_id)
    return operation.institution_id if operation else None


def _lock_work_session(session_id: UUID) -> None:
    """SELECT ... FOR UPDATE on the work_session row, re-read from PostgreSQL.

    The serialization point shared with SessionService: its Supervisor writes (inventory and
    confirmed additions, resolve, close, edit, privacy) lock the same row first. An Operator
    transaction and a Supervisor change of one session therefore never interleave: the second one
    waits, then evaluates the state the first one committed (e.g. an inventory edit after the
    report answers 409; a report after an addition counts the new reference).
    """
    db.session.execute(
        select(WorkSession).where(WorkSession.id == session_id).with_for_update()
        .execution_options(populate_existing=True)
    )


def _require_session_in_tenant(session_id: UUID, institution_id: UUID | None) -> WorkSession:
    work = db.session.get(WorkSession, session_id)
    if work is None or institution_id is None or _session_tenant_id(work) != institution_id:
        raise RfSessionError(N_("Session not found."), 404)
    return work


def _validate_operator_for_tenant(operator_user_id: UUID, institution_id: UUID) -> None:
    """Active user of the tenant holding station_operator through a role of that same tenant."""
    operator = db.session.get(User, operator_user_id)
    if operator is None or not operator.active or operator.institution_id != institution_id:
        raise RfSessionError(N_("Choose a valid operator."), 400)
    role_ok = db.session.scalar(
        select(UserRole)
        .join(Role, Role.id == UserRole.role_id)
        .where(
            UserRole.user_id == operator_user_id,
            Role.code == "station_operator",
            Role.institution_id == institution_id,
        )
    )
    if role_ok is None:
        raise RfSessionError(N_("Assigned user must have station_operator role."), 400)


def _validate_room_station_for_tenant(room_id: UUID, station_id: UUID, institution_id: UUID) -> None:
    room = db.session.get(OperatingRoom, room_id)
    if room is None or room.institution_id != institution_id:
        raise RfSessionError(N_("Choose a valid operating room."), 400)
    station = db.session.get(CaptureStation, station_id)
    if station is None or station.room_id != room.id:
        raise RfSessionError(N_("Choose a capture station in the selected operating room."), 400)


def _validate_kit_for_tenant(kit_id: UUID, institution_id: UUID) -> None:
    kit = db.session.get(Kit, kit_id)
    if kit is None or kit.institution_id != institution_id:
        raise RfSessionError(N_("Choose a valid kit."), 400)


def _validate_patient_for_tenant(patient_id: UUID | None, institution_id: UUID) -> None:
    if patient_id is None:
        return
    patient = db.session.get(Patient, patient_id)
    if patient is None or patient.institution_id != institution_id:
        raise RfSessionError(N_("Choose a valid patient."), 400)


def start_session(*, session_id: UUID, operator_user_id: UUID, institution_id: UUID | None, ip: str | None = None) -> dict:
    """RF-OP-03 Start Session transaction (no evidence/WSS in this stage)."""
    _lock_work_session(session_id)
    work = _require_session_in_tenant(session_id, institution_id)
    if work.user_id != operator_user_id:
        raise RfSessionError(N_("Session is not assigned to you."), 403)
    status = db.session.get(CatSessionStatus, work.status_id)
    if status is None or status.code != "scheduled":
        raise RfSessionError(N_("Session is no longer scheduled."), 409)

    has_agreement = db.session.scalar(
        select(SessionProcessingAgreement.id).where(SessionProcessingAgreement.session_id == session_id)
    ) is not None
    capture_mode = "vision" if has_agreement else "manual_no_privacy"
    now = utc_now()

    work.status_id = _status_id("in_progress")
    work.started_at = now
    work.phase_changed_at = now
    work.updated_at = now
    work.capture_mode = capture_mode
    if work.current_phase_id is None:
        start_phase = db.session.scalar(
            select(CatOperationPhase).where(CatOperationPhase.code == "start")
        )
        if start_phase:
            work.current_phase_id = start_phase.id

    # Re-freeze expected inventory (idempotent replace of draft rows).
    existing = db.session.execute(
        select(ExpectedInventory).where(ExpectedInventory.session_id == session_id)
    ).scalars().all()
    if not existing and work.kit_id:
        kit_items = db.session.execute(
            select(KitItem).where(KitItem.kit_id == work.kit_id)
        ).scalars().all()
        for item in kit_items:
            db.session.add(ExpectedInventory(
                session_id=session_id,
                family_id=item.family_id,
                expected_quantity=item.quantity,
                source="kit_snapshot",
            ))
    # If draft rows already exist from SP-02, keep them (already frozen content).

    phase = db.session.get(CatOperationPhase, work.current_phase_id) if work.current_phase_id else None
    payload = {
        "phase_id": str(work.current_phase_id) if work.current_phase_id else None,
        "phase_code": phase.code if phase else None,
        "capture_mode": capture_mode,
        "source": "station",
    }
    db.session.add(CountEvent(
        event_type_id=_event_type_id("session_open"),
        session_id=session_id,
        user_id=operator_user_id,
        occurred_at=now,
        payload=payload,
    ))
    _audit(
        user_id=operator_user_id,
        action="session.start",
        resource_id=session_id,
        institution_id=institution_id,
        ip=ip,
    )
    try:
        db.session.commit()
    except SQLAlchemyError as exc:
        db.session.rollback()
        raise RfSessionError(N_("Could not start session; please retry."), 503) from exc

    if capture_mode == "vision":
        try:
            from services.vision_bridge import VisionBridgeError, bind_session as vision_bind
            vision_bind(session_id)
        except Exception:
            # Worker may be down; OP capture UI shows offline banner and can retry bind.
            pass

    return {"capture_mode": capture_mode, "session_id": str(session_id)}


def ai_suggestion_for_session(session_id: UUID) -> tuple[UUID, dict[str, int]] | None:
    """(auto_count id, {family_id: AI detected}) of the session's last final AI count, or None.

    DS04 §1.3.6: the AI suggestion of a family is its detected_quantity in the last auto_count
    with trigger=final (0 if the family is missing from that lot). Rows written before
    `trigger` existed are recognised by reason=detection_close (the Finish counting snapshot).
    """
    rows = db.session.execute(
        select(CountEvent)
        .join(CatEventType, CatEventType.id == CountEvent.event_type_id)
        .where(
            CountEvent.session_id == session_id,
            CatEventType.code == "auto_count",
            CountEvent.family_id.is_(None),
        )
        .order_by(CountEvent.occurred_at.desc())
    ).scalars()
    for event in rows:
        payload = event.payload if isinstance(event.payload, dict) else {}
        is_final = payload.get("trigger") == "final" or (
            "trigger" not in payload and payload.get("reason") == "detection_close"
        )
        if not is_final:
            continue
        lot = (payload.get("board") or {}).get("families") or payload.get("counts") or []
        suggestion = {}
        for item in lot:
            if isinstance(item, dict) and item.get("family_id"):
                suggestion[str(item["family_id"])] = int(item.get("detected_quantity") or 0)
        return event.id, suggestion
    return None


# Why a vision session falls back to the manual quantity report (contingency count).
CONTINGENCY_REASONS = ("worker_unavailable", "processing_error", "unreliable_detection", "other")
_CONTINGENCY_NOTES_REQUIRED = {"unreliable_detection", "other"}


def _contingency_values(contingency: dict | None) -> dict:
    contingency = contingency or {}
    reason = (contingency.get("reason_code") or "").strip()
    notes = (contingency.get("notes") or "").strip()
    if reason not in CONTINGENCY_REASONS:
        raise RfSessionError(N_("Choose why YOLO could not be used for this count."), 400)
    if reason in _CONTINGENCY_NOTES_REQUIRED and len(notes) < 10:
        raise RfSessionError(N_("Describe the contingency in at least 10 characters."), 400)
    return {"reason_code": reason, "notes": notes or None,
            "worker_state": contingency.get("worker_state") or None}


def submit_manual_close(
    *,
    session_id: UUID,
    operator_user_id: UUID,
    institution_id: UUID | None,
    reports: list[dict],
    ip: str | None = None,
    close_path: str = "manual_report",
    contingency: dict | None = None,
    extra_instruments: int = 0,
    extra_notes: str = "",
) -> dict:
    """RF-OP-06 / 06M: the Operator submits the verified quantities and ends their part.

    This never closes the session safely: the result is awaiting_spd_review (SPD confirms the
    close) or correction_required (SPD reconciles discrepancies first).

    Vision sessions:
    - close_path="vision_validation": human validation of a final YOLO count that matches the
      current reference count (initial + confirmed additions); the AI suggestion is compared
      family by family (human_correction).
    - close_path="manual_report": contingency count when YOLO cannot be used. A reason is
      mandatory and recorded with user, time, verified quantities and the worker state. It can
      never hide a physical difference: a total below the reference, or a final count YOLO
      already rejected, opens a discrepancy for SPD reconciliation.
    extra_instruments: instruments of families outside the kit confirmed on the tray (they are
    part of the physical total and always need SPD handling).
    manual_no_privacy sessions keep their rules (no reference, no contingency).
    """
    _lock_work_session(session_id)
    work = _require_session_in_tenant(session_id, institution_id)
    if work.user_id != operator_user_id:
        raise RfSessionError(N_("Session is not assigned to you."), 403)
    status = db.session.get(CatSessionStatus, work.status_id)
    if status is None or status.code != "in_progress":
        raise RfSessionError(N_("Session is not in progress."), 409)
    # Vision sessions may use the quantity-report path until the video/YOLO feed is wired.
    if work.capture_mode not in {"manual_no_privacy", "vision"}:
        raise RfSessionError(N_("Quantity report is not allowed for this capture mode."), 409)

    expected_rows = db.session.execute(
        select(ExpectedInventory).where(ExpectedInventory.session_id == session_id)
    ).scalars().all()
    if not expected_rows:
        raise RfSessionError(N_("Session has no expected inventory."), 409)

    by_family = {str(item.family_id): item for item in expected_rows}
    if set(by_family) != {str(r["family_id"]) for r in reports}:
        raise RfSessionError(N_("Report must include every expected family exactly once."), 400)

    vision = work.capture_mode == "vision"
    final_state = vision_final_state(session_id) if vision else None
    if vision and close_path == "vision_validation":
        if not final_state["confirmed"]:
            raise RfSessionError(N_("The final YOLO count does not match the current reference count. "
                                    "Capture the tray again before validating."), 409)
        contingency = None
    elif vision:
        contingency = _contingency_values(contingency)
    else:
        contingency, extra_instruments, extra_notes = None, 0, ""
    try:
        extra_instruments = int(extra_instruments or 0)
    except (TypeError, ValueError):
        raise RfSessionError(N_("Enter a whole number of instruments outside the kit."), 400) from None
    if extra_instruments < 0:
        raise RfSessionError(N_("Enter a whole number of instruments outside the kit."), 400)
    extra_notes = (extra_notes or "").strip()

    # Human validation compares the human count with the frozen final AI count (DS04
    # human_correction). Without a valid AI count (manual_no_privacy, contingency report) the
    # legacy portal rule stays: a correction is recorded when the report differs from expected.
    ai_event_id, ai_by_family = None, None
    if vision and final_state["confirmed"] and contingency is None:
        found = ai_suggestion_for_session(session_id)
        if found is not None:
            ai_event_id, ai_by_family = found

    now = utc_now()
    open_discrepancies = 0
    shortage_reason = _discrepancy_reason_id("shortage")
    surplus_reason = _discrepancy_reason_id("surplus")
    manual_count_type = _event_type_id("manual_count")
    manual_close_type = _event_type_id("manual_close")

    for report in reports:
        family_id = UUID(str(report["family_id"]))
        expected = by_family[str(family_id)]
        reported = int(report["reported_quantity"])
        if reported < 0:
            raise RfSessionError(N_("Reported quantity must be >= 0."), 400)
        reason_code = (report.get("reason_code") or "").strip()
        notes = (report.get("notes") or "").strip()
        diff = reported - expected.expected_quantity
        if diff != 0 and not reason_code:
            raise RfSessionError(N_("Reason is required when reported quantity differs from expected."), 400)
        if reason_code == "other" and len(notes) < 10:
            raise RfSessionError(N_("Notes must have at least 10 characters when reason is Other."), 400)
        ai_suggested = None if ai_by_family is None else ai_by_family.get(str(family_id), 0)
        corrected = diff != 0 if ai_suggested is None else reported != ai_suggested
        if ai_suggested is not None and corrected and not (reason_code or notes):
            raise RfSessionError(N_("Explain why the confirmed count differs from the AI detection."), 400)

        payload = {
            "ai": work.capture_mode == "vision",
            "capture_mode": work.capture_mode or "manual_no_privacy",
            # Kept for readers of legacy rows; DS04 source is detected_quantity.
            "reported_quantity": reported,
            "reason_code": reason_code or None,
            "notes": notes or None,
            "close_path": "manual_count",
        }
        if ai_suggested is not None:
            payload["ai_suggested_quantity"] = ai_suggested
            payload["ai_count_event_id"] = str(ai_event_id)
        event = CountEvent(
            event_type_id=manual_count_type,
            session_id=session_id,
            user_id=operator_user_id,
            family_id=family_id,
            expected_quantity=expected.expected_quantity,
            # DS04 §1.3.6: in manual_count this is the quantity reported by the human.
            detected_quantity=reported,
            occurred_at=now,
            payload=payload,
        )
        db.session.add(event)
        db.session.flush()

        if corrected:
            justification = f"{reason_code}: {notes}".strip(": ")
            db.session.add(HumanCorrection(
                justification=justification or reason_code,
                recorded_at=now,
                count_event_id=event.id,
                user_id=operator_user_id,
            ))

        if reported < expected.expected_quantity:
            family = db.session.get(InstrumentFamily, family_id)
            db.session.add(Discrepancy(
                description=(
                    f"Shortfall on {family.name if family else family_id}: "
                    f"expected {expected.expected_quantity}, reported {reported}."
                ),
                resolved=False,
                reason_id=shortage_reason,
                family_id=family_id,
                expected_quantity=expected.expected_quantity,
                detected_quantity=reported,
                session_id=session_id,
                origin_event_id=event.id,
            ))
            open_discrepancies += 1
        elif reported > expected.expected_quantity and not EXTRA_AUTO_ACK_ON_MANUAL_CLOSE:
            family = db.session.get(InstrumentFamily, family_id)
            db.session.add(Discrepancy(
                description=(
                    f"Extra on {family.name if family else family_id}: "
                    f"expected {expected.expected_quantity}, reported {reported}."
                ),
                resolved=False,
                reason_id=surplus_reason,
                family_id=family_id,
                expected_quantity=expected.expected_quantity,
                detected_quantity=reported,
                session_id=session_id,
                origin_event_id=event.id,
            ))
            open_discrepancies += 1

    # Vision sessions: the physical total must reach the reference count (initial YOLO count +
    # confirmed additions). Family shortages already raised above explain part of a gap; what
    # they do not explain is an instrument nobody accounted for: never a safe close.
    reference = final_state["reference"] if final_state else None
    family_shortfall = sum(
        max(0, by_family[str(r["family_id"])].expected_quantity - int(r["reported_quantity"])) for r in reports
    )
    family_surplus = sum(
        max(0, int(r["reported_quantity"]) - by_family[str(r["family_id"])].expected_quantity) for r in reports
    )
    human_total = sum(int(r["reported_quantity"]) for r in reports) + extra_instruments
    # A difference YOLO reported (rejected final count, or a final count confirmed with the
    # difference acknowledged) is never overruled by the human numbers, whatever the path.
    known = final_state["known_mismatch"] if final_state else None
    known_context = ("before the contingency report" if contingency is not None
                     else "(last frame, confirmed by the Operator)")
    if reference is not None:
        missing_human = reference["total"] - human_total - family_shortfall
        # A final count YOLO rejected before this contingency report is a known physical
        # difference: a manual report cannot overrule it (measured against its own reference).
        missing_yolo = (known["reference"] - known["final"] - family_shortfall) if known else 0
        if max(missing_human, missing_yolo) > 0:
            notes = [f"reference {reference['total']} (initial {reference['initial_total']} "
                     f"+ {reference['added_total']} added), confirmed {human_total}"]
            if missing_yolo > 0:
                notes.append(f"YOLO final capture {known['final']} of {known['reference']} {known_context}")
            db.session.add(Discrepancy(
                description="Instruments missing against the reference count: " + "; ".join(notes) + ".",
                resolved=False,
                reason_id=shortage_reason,
                family_id=None,
                expected_quantity=reference["total"],
                detected_quantity=human_total,
                session_id=session_id,
                origin_event_id=known["event_id"] if missing_yolo > 0 else reference["initial"]["event_id"],
            ))
            open_discrepancies += 1
        extra_yolo = (known["final"] - known["reference"] - family_surplus - extra_instruments) if known else 0
        if extra_yolo > 0:
            db.session.add(Discrepancy(
                description=(f"YOLO final capture detected {known['final']} instruments against a reference of "
                             f"{known['reference']} {known_context}; the report does not account "
                             f"for {extra_yolo}."),
                resolved=False,
                reason_id=surplus_reason,
                family_id=None,
                expected_quantity=known["reference"],
                detected_quantity=known["final"],
                session_id=session_id,
                origin_event_id=known["event_id"],
            ))
            open_discrepancies += 1
    if extra_instruments:
        db.session.add(Discrepancy(
            description=(f"Instruments outside the kit confirmed on the tray: {extra_instruments}."
                         + (f" {extra_notes}" if extra_notes else "")),
            resolved=False,
            reason_id=surplus_reason,
            family_id=None,
            expected_quantity=0,
            detected_quantity=extra_instruments,
            session_id=session_id,
            origin_event_id=final_state["final"]["event_id"] if final_state and final_state["final"] else None,
        ))
        open_discrepancies += 1

    next_status = "correction_required" if open_discrepancies else "awaiting_spd_review"
    capture_mode = work.capture_mode or "manual_no_privacy"
    work.status_id = _status_id(next_status)
    work.ended_at = now
    work.updated_at = now
    db.session.add(CountEvent(
        event_type_id=manual_close_type,
        session_id=session_id,
        user_id=operator_user_id,
        occurred_at=now,
        payload={
            "ai": capture_mode == "vision",
            "capture_mode": capture_mode,
            "open_discrepancies": open_discrepancies,
            "next_status": next_status,
            "close_path": "manual_count",
            # The Operator's part ends here; the safe close is the SPD confirmation (session_close).
            "operator_close_path": close_path if vision else "manual_report",
            "operator_finished": True,
            "safe_close": False,
            "verified_total": human_total if vision else None,
            "extra_instruments": extra_instruments or None,
            "reference": ({"total": reference["total"], "initial_total": reference["initial_total"],
                           "added_total": reference["added_total"]} if reference else None),
            "contingency": contingency,
            "known_final_mismatch": ({"final": known["final"], "reference": known["reference"],
                                      "event_id": str(known["event_id"])} if known else None),
        },
    ))
    _audit(
        user_id=operator_user_id,
        action="session.contingency_close" if contingency else "session.manual_close",
        resource_id=session_id,
        institution_id=institution_id,
        ip=ip,
    )
    try:
        db.session.commit()
    except SQLAlchemyError as exc:
        db.session.rollback()
        raise RfSessionError(N_("Could not submit report; please retry."), 503) from exc

    if capture_mode == "vision":
        try:
            from services.vision_bridge import unbind_session as vision_unbind
            vision_unbind(session_id)
        except Exception:
            pass
        clear_vision_snapshot(session_id)

    result = {
        "session_id": str(session_id),
        "status_code": next_status,
        "open_discrepancies": open_discrepancies,
    }
    if contingency is not None:
        result["contingency"] = True
    return result


def contingency_for_session(session_id: UUID) -> dict | None:
    """Contingency count of the session (reason, Operator, time, verified total) or None."""
    closes = _count_events(session_id, "manual_close")
    if not closes:
        return None
    last = closes[-1]
    payload = last.payload if isinstance(last.payload, dict) else {}
    if not payload.get("contingency"):
        return None
    user = db.session.get(User, last.user_id) if last.user_id else None
    return {**payload["contingency"], "event_id": str(last.id), "occurred_at": last.occurred_at,
            "user_id": str(last.user_id) if last.user_id else None, "user_name": user.name if user else None,
            "verified_total": payload.get("verified_total"), "reference": payload.get("reference"),
            "known_final_mismatch": payload.get("known_final_mismatch")}


# In-process snapshot for RF-OP-05/06 after Finish counting (no Mongo this cycle).
_VISION_SNAPSHOTS: dict[str, dict] = {}
_AUTO_COUNT_FLUSHED: set[str] = set()


def store_vision_snapshot(session_id: UUID | str, snapshot: dict) -> None:
    _VISION_SNAPSHOTS[str(session_id)] = snapshot


def get_vision_snapshot(session_id: UUID | str) -> dict | None:
    """Frozen final count for OP-05/06: process cache, else rebuilt from PostgreSQL.

    The cache only adds the key-frame image; after a Flask restart the summary and validation
    come back from the final auto_count (DS04), without MongoDB.
    """
    cached = _VISION_SNAPSHOTS.get(str(session_id))
    if cached is not None:
        return cached
    try:
        sid = UUID(str(session_id))
    except (TypeError, ValueError):
        return None
    return vision_snapshot_from_pg(sid)


def vision_snapshot_from_pg(session_id: UUID) -> dict | None:
    state = vision_final_state(session_id)
    if not state["confirmed"]:
        return None
    event = db.session.get(CountEvent, state["final"]["event_id"])
    payload = event.payload if event is not None and isinstance(event.payload, dict) else {}
    counts = {}
    for family in (payload.get("board") or {}).get("families") or []:
        code = family.get("family_code")
        if code:
            counts[code] = {key: family.get(key) for key in (
                "family_id", "family_code", "family_name", "category_name", "expected_quantity",
                "detected_quantity", "raw_detected_quantity", "best_confidence", "weakest_confidence",
                "avg_confidence")}
    reference = state["reference"]
    return {
        **_edge_comparison(reference, payload, state),
        "stage": "final",
        "session_id": str(session_id),
        "model_version": payload.get("model_version"),
        "conf_threshold": (payload.get("pipeline_config") or {}).get("conf_threshold"),
        "pipeline": payload.get("pipeline") or "matrix_v1",
        "assign_solver": (payload.get("pipeline_config") or {}).get("solver"),
        "frame_jpeg_b64": payload.get("frame_jpeg_b64"),
        "boxes": [],
        "counts": counts,
        "raw_counts": {},
        "assignment_metrics": payload.get("assignment_metrics") or {},
        "initial_total": reference["initial_total"],
        "added_total": reference["added_total"],
        "reference_total": reference["total"],
        "final_total": state["final"]["total"],
        "out_of_kit": payload.get("out_of_kit") or [],
        "finished_at": event.occurred_at.isoformat() if event is not None else None,
        "restored_from": "postgresql",
    }


def _lot_families(payload: dict | None) -> dict[str, int]:
    """{family_id: detected} of a board lot."""
    return {str(item["family_id"]): int(item.get("detected_quantity") or 0)
            for item in ((payload or {}).get("board") or {}).get("families") or []
            if isinstance(item, dict) and item.get("family_id")}


def _edge_comparison(reference: dict, final_payload: dict, state: dict) -> dict:
    """Initial vs final YOLO lots for human validation (both frozen in PostgreSQL)."""
    initial_event = db.session.get(CountEvent, reference["initial"]["event_id"])
    initial_payload = initial_event.payload if initial_event is not None and isinstance(initial_event.payload, dict) else {}
    return {
        "initial_counts": _lot_families(initial_payload),
        "final_counts": _lot_families(final_payload),
        "initial_frame_index": initial_payload.get("frame_index"),
        "final_frame_index": final_payload.get("frame_index"),
        "initial_confirmed_at": initial_payload.get("confirmed_at"),
        "final_confirmed_at": final_payload.get("confirmed_at"),
        "video": final_payload.get("video") or initial_payload.get("video"),
        "single_video": initial_payload.get("source") == "first_frame" and final_payload.get("source") == "last_frame",
        "matches_reference": bool(state.get("matches")),
        "initial_out_of_kit": initial_payload.get("out_of_kit") or [],
    }


def clear_auto_count_flush_keys_for_session(session_id: UUID | str) -> None:
    sid = str(session_id)
    stale = [key for key in _AUTO_COUNT_FLUSHED if key.startswith(f"{sid}:")]
    for key in stale:
        _AUTO_COUNT_FLUSHED.discard(key)


def clear_vision_snapshot(session_id: UUID | str) -> None:
    sid = str(session_id)
    _VISION_SNAPSHOTS.pop(sid, None)
    clear_auto_count_flush_keys_for_session(sid)


def change_phase_stub(
    *,
    session_id: UUID,
    operator_user_id: UUID,
    institution_id: UUID | None,
    to_phase_code: str,
    ip: str | None = None,
    worker_session: dict | None = None,
) -> dict:
    """Real phase UPDATE + count_event; no Mongo/Garage side effects in this demo cycle."""
    work = _require_session_in_tenant(session_id, institution_id)
    if work.user_id != operator_user_id:
        raise RfSessionError(N_("Session is not assigned to you."), 403)
    status = db.session.get(CatSessionStatus, work.status_id)
    if status is None or status.code != "in_progress":
        raise RfSessionError(N_("Session is not in progress."), 409)
    to_phase = db.session.scalar(select(CatOperationPhase).where(CatOperationPhase.code == to_phase_code))
    if to_phase is None:
        raise RfSessionError(N_("Unknown phase code."), 400)
    from_phase = db.session.get(CatOperationPhase, work.current_phase_id) if work.current_phase_id else None
    now = utc_now()
    work.current_phase_id = to_phase.id
    work.phase_changed_at = now
    work.updated_at = now
    db.session.add(CountEvent(
        event_type_id=_event_type_id("phase_change"),
        session_id=session_id,
        user_id=operator_user_id,
        occurred_at=now,
        payload={
            "from_phase": from_phase.code if from_phase else None,
            "to_phase": to_phase.code,
            "phase_id": str(to_phase.id),
            "phase_code": to_phase.code,
            "capture_mode": work.capture_mode,
            "source": "station_button",
            "stub": True,
        },
    ))
    try:
        db.session.commit()
    except SQLAlchemyError as exc:
        db.session.rollback()
        raise RfSessionError(N_("Could not change phase; please retry."), 503) from exc
    if work.capture_mode == "vision":
        # DS06 state_change: key frame of the current run when the worker has one.
        record_vision_checkpoint(work, "state_change", worker_session)
    return {
        "session_id": str(session_id),
        "phase_code": to_phase.code,
        "phase_name": localize_db_label("operation_phase", to_phase.code, to_phase.name),
    }


_BOARD_METRIC_KEYS = (
    "solver",
    "extras_raw",
    "extras_refined",
    "missing_raw",
    "missing_refined",
    "reassign_count",
    "type_degrade_count",
    "family_match_rate_raw",
    "family_match_rate_refined",
)


def _compact_box_audit(box: dict) -> dict:
    """Keep observable box telemetry; drop recalculable matrix internals."""
    if not isinstance(box, dict):
        return {}
    # Prefer already-compact audit shape from worker; fall back to public box fields.
    if "yolo_top1" in box or "assigned" in box:
        topk = box.get("topk") or []
        return {
            "xyxy": box.get("xyxy"),
            "yolo_top1": box.get("yolo_top1"),
            "assigned": box.get("assigned"),
            "topk": topk[:4] if isinstance(topk, list) else [],
        }
    raw = box.get("raw") or {}
    refined = box.get("refined") or {}
    topk = raw.get("topk") or []
    return {
        "xyxy": box.get("xyxy"),
        "yolo_top1": {
            "family_code": raw.get("family_code") or box.get("family_code"),
            "family_id": raw.get("family_id") or box.get("family_id"),
            "score": raw.get("confidence") if raw.get("confidence") is not None else box.get("confidence"),
        },
        "assigned": {
            "family_code": refined.get("family_code") or box.get("family_code"),
            "family_id": refined.get("family_id") or box.get("family_id"),
            "score": refined.get("visual_score") if refined.get("visual_score") is not None else box.get("confidence"),
            "reason": refined.get("resolution_reason") or box.get("resolution_reason"),
        },
        "topk": topk[:4] if isinstance(topk, list) else [],
    }


def build_board_payload_from_counts(
    counts: dict,
    *,
    include_boxes: bool = False,
    sample_meta: dict | None = None,
    model_version: str | None = None,
    reason: str = "video_window",
) -> tuple[dict, int, int]:
    """Build one board-scoped payload. Returns (payload, expected_total, detected_total)."""
    meta_extra = sample_meta or {}
    families: list[dict] = []
    expected_total = 0
    detected_total = 0
    raw_total = 0
    for code, meta in (counts or {}).items():
        if not isinstance(meta, dict):
            continue
        expected = int(meta.get("expected_quantity") or 0)
        detected = int(meta.get("detected_quantity") or 0)
        raw_n = int(meta.get("raw_detected_quantity") or 0)
        expected_total += expected
        detected_total += detected
        raw_total += raw_n
        row = {
            "family_id": meta.get("family_id"),
            "family_code": meta.get("family_code") or code,
            "family_name": meta.get("family_name"),
            "category_name": meta.get("category_name"),
            "expected_quantity": expected,
            "raw_detected_quantity": raw_n,
            "detected_quantity": detected,
            "difference": meta.get("difference"),
            "best_confidence": meta.get("best_confidence"),
            "weakest_confidence": meta.get("weakest_confidence"),
            "avg_confidence": meta.get("avg_confidence"),
            "held": bool(meta.get("held")),
            "sticky_name": meta.get("sticky_name"),
            "reassigned_in": meta.get("reassigned_in"),
            "reassigned_out": meta.get("reassigned_out"),
        }
        boxes = meta.get("boxes") or []
        if include_boxes and boxes:
            row["boxes"] = [_compact_box_audit(b) for b in boxes if isinstance(b, dict)]
        else:
            row["box_count"] = int(meta.get("box_count") or len(boxes))
        families.append(row)

    families.sort(key=lambda r: str(r.get("family_code") or ""))
    pipeline = meta_extra.get("pipeline") or "matrix_v1"
    solver = meta_extra.get("assign_solver") or meta_extra.get("solver")
    am = meta_extra.get("assignment_metrics") or {}
    payload = {
        "ai": True,
        "capture_mode": "vision",
        "scope": "board",
        "reason": reason,
        # DS04: the AI suggestion is the last auto_count with trigger=final (Finish counting).
        # "initial" (reference count at session start) extends the DS04 trigger vocabulary.
        "trigger": VISION_TRIGGERS.get(reason, "stable_window"),
        "model_version": model_version or meta_extra.get("model_version"),
        "pipeline": pipeline,
        "pipeline_config": {
            "pipeline": pipeline,
            "solver": solver,
            "count_sample_every": meta_extra.get("count_sample_every"),
            "conf_threshold": meta_extra.get("conf_threshold"),
        },
        "run_id": meta_extra.get("run_id"),
        "frame_index": meta_extra.get("frame_index"),
        "frames_processed": meta_extra.get("frames_processed"),
        "video_t": meta_extra.get("video_t"),
        "board": {
            "families": families,
            "totals": {
                "expected": expected_total,
                "detected": detected_total,
                "raw": raw_total,
                "family_count": len(families),
            },
        },
    }
    if isinstance(am, dict) and am:
        payload["assignment_metrics"] = {k: am.get(k) for k in _BOARD_METRIC_KEYS if k in am}
    return payload, expected_total, detected_total


def persist_board_auto_count(
    *,
    session_id: UUID,
    operator_user_id: UUID,
    counts: dict,
    model_version: str | None = None,
    reason: str = "video_window",
    once_key: str | None = None,
    include_boxes: bool = False,
    sample_meta: dict | None = None,
    capture_total: int | None = None,
    out_of_kit: list | None = None,
    extra_payload: dict | None = None,
) -> int:
    """Insert one auto_count row for a vision board snapshot (family_id NULL).

    extra_payload (edge counts of the single-video flow): video identity, frame index, frame quality
    and the Operator's confirmation, merged into the payload.

    capture_total (initial / final lots): instruments in the last valid capture, stored as
    detected_quantity. The per-family board tally is capped by the kit quotas, so its sum can
    hide extra instruments; the board totals stay in the payload.

    Dense telemetry stays in worker NDJSON / future Mongo (DS06). PG stores observables:
    raw tallies, compact top-k boxes (optional), and exposed final table — not the score matrix.
    """
    flush_key = once_key or f"{session_id}:{reason}"
    if flush_key in _AUTO_COUNT_FLUSHED and reason not in VISION_TRIGGERS:
        return 0
    work = db.session.get(WorkSession, session_id)
    if work is None:
        raise RfSessionError(N_("Session not found."), 404)
    if not counts:
        return 0

    payload, expected_total, detected_total = build_board_payload_from_counts(
        counts,
        include_boxes=include_boxes,
        sample_meta=sample_meta,
        model_version=model_version,
        reason=reason,
    )
    if not payload.get("board", {}).get("families"):
        return 0
    if capture_total is not None:
        payload["capture_total"] = int(capture_total)
        detected_total = int(capture_total)
    if out_of_kit:
        # Instruments of families outside the kit: in the total, unassigned, for human review.
        payload["out_of_kit"] = [
            {key: item.get(key) for key in ("family_id", "family_code", "family_name", "count", "best_confidence")}
            for item in out_of_kit if isinstance(item, dict)
        ]
    if extra_payload:
        payload.update(extra_payload)

    db.session.add(CountEvent(
        event_type_id=_event_type_id("auto_count"),
        session_id=session_id,
        user_id=operator_user_id,
        family_id=None,
        expected_quantity=expected_total,
        detected_quantity=detected_total,
        occurred_at=utc_now(),
        payload=payload,
    ))
    try:
        db.session.commit()
    except SQLAlchemyError as exc:
        db.session.rollback()
        raise RfSessionError(N_("Could not persist board auto count."), 503) from exc
    _AUTO_COUNT_FLUSHED.add(flush_key)
    return 1


def persist_auto_counts_from_worker(
    *,
    session_id: UUID,
    operator_user_id: UUID,
    counts: dict,
    model_version: str | None = None,
    reason: str = "video_window",
    once_key: str | None = None,
    include_boxes: bool = True,
    sample_meta: dict | None = None,
) -> int:
    """Compatibility wrapper → board-scoped auto_count (one row per sample)."""
    return persist_board_auto_count(
        session_id=session_id,
        operator_user_id=operator_user_id,
        counts=counts,
        model_version=model_version,
        reason=reason,
        once_key=once_key,
        include_boxes=include_boxes,
        sample_meta=sample_meta,
    )


def persist_vision_timeline_windows(
    *,
    session_id: UUID,
    operator_user_id: UUID,
    samples: list[dict],
    model_version: str | None = None,
) -> int:
    """Persist one board auto_count per timeline sample (reason=video_window)."""
    total = 0
    for sample in samples or []:
        run_id = sample.get("run_id") or "run"
        frames_processed = sample.get("frames_processed")
        if frames_processed is None:
            continue
        counts = sample.get("counts") or {}
        if not counts:
            continue
        written = persist_board_auto_count(
            session_id=session_id,
            operator_user_id=operator_user_id,
            counts=counts,
            model_version=model_version or sample.get("model_version"),
            reason="video_window",
            once_key=f"{session_id}:video_window:{run_id}:{frames_processed}",
            include_boxes=bool(sample.get("include_boxes")),
            sample_meta={
                "run_id": run_id,
                "frame_index": sample.get("frame_index"),
                "frames_processed": frames_processed,
                "video_t": sample.get("video_t"),
                "pipeline": sample.get("pipeline"),
                "assign_solver": sample.get("assign_solver"),
                "model_version": sample.get("model_version"),
                "assignment_metrics": sample.get("assignment_metrics") or {},
                "count_sample_every": sample.get("count_sample_every"),
                "conf_threshold": sample.get("conf_threshold"),
            },
        )
        total += written
    return total


def vision_result_problem(worker_session: dict | None) -> str | None:
    """Why the worker's current run cannot be frozen as the AI count (None = usable).

    Only a completed run (status=ready) of the current upload counts: after an error or a
    cancel the worker may still expose the tallies of an earlier run, so counts alone prove
    nothing. Messages are N_ msgids (routes translate them).
    """
    worker_session = worker_session or {}
    status = worker_session.get("status")
    if status == "processing":
        return N_("The video is still being processed. Wait until processing finishes.")
    if status == "error":
        return N_("Video processing failed. Upload the video again, or use the quantity report.")
    if status != "ready" or not worker_session.get("run_id"):
        return N_("Upload and process a video before finishing the count.")
    if not int(worker_session.get("frames_processed") or 0) or not worker_session.get("counts"):
        return N_("The processed video produced no count. Upload another video, or use the quantity report.")
    return None


# auto_count triggers of frozen lots (Finish counting); every other board lot is stable_window.
VISION_TRIGGERS = {"detection_initial": "initial", "detection_close": "final"}


def vision_capture_total(worker_session: dict | None) -> int:
    """Instruments in the last valid capture of the worker's current run.

    Every box of the final (stabilised) frame counts, whether assigned to a family or only to
    an instrument type: the per-family counts are capped by the kit quotas, so their sum could
    hide an extra instrument. Classification is reviewed later by the Operator.
    """
    return sum(1 for box in (worker_session or {}).get("boxes") or [] if isinstance(box, dict))


def initial_vision_count(session_id: UUID) -> dict | None:
    """Reference count of the session: its first auto_count with trigger=initial (or None)."""
    rows = db.session.execute(
        select(CountEvent)
        .join(CatEventType, CatEventType.id == CountEvent.event_type_id)
        .where(
            CountEvent.session_id == session_id,
            CatEventType.code == "auto_count",
            CountEvent.family_id.is_(None),
        )
        .order_by(CountEvent.occurred_at)
    ).scalars()
    for event in rows:
        payload = event.payload if isinstance(event.payload, dict) else {}
        if payload.get("trigger") == "initial":
            video = payload.get("video") or {}
            return {"event_id": event.id, "total": int(event.detected_quantity or 0),
                    "run_id": payload.get("run_id"), "occurred_at": event.occurred_at,
                    # Single-video flow (source=first_frame): the video both edge counts come from.
                    "source": payload.get("source"), "video_id": video.get("video_id"),
                    "video_sha256": video.get("sha256"), "frame_index": payload.get("frame_index"),
                    "user_id": event.user_id}
    return None


def _count_events(session_id: UUID, code: str, *, family_scoped: bool | None = None):
    query = (
        select(CountEvent)
        .join(CatEventType, CatEventType.id == CountEvent.event_type_id)
        .where(CountEvent.session_id == session_id, CatEventType.code == code)
        .order_by(CountEvent.occurred_at)
    )
    if family_scoped is False:
        query = query.where(CountEvent.family_id.is_(None))
    return db.session.execute(query).scalars().all()


def count_reference(session_id: UUID) -> dict | None:
    """Current vision count reference: initial YOLO count + confirmed additions after it.

    Only instruments_added events (instruments physically added to the tray and confirmed by
    the SPD Supervisor while the session is in progress) raise it. Additions confirmed before
    the initial count are already on the initial capture; administrative inventory edits never
    count. None until the initial count is registered.
    """
    initial = initial_vision_count(session_id)
    if initial is None:
        return None
    additions = [event for event in _count_events(session_id, "instruments_added")
                 if event.occurred_at > initial["occurred_at"]]
    added = sum(int(event.detected_quantity or 0) for event in additions)
    return {
        "initial": initial,
        "initial_total": initial["total"],
        "added_total": added,
        "total": initial["total"] + added,
        "changed_at": additions[-1].occurred_at if additions else initial["occurred_at"],
        "addition_event_ids": [str(event.id) for event in additions],
    }


def vision_final_state(session_id: UUID) -> dict:
    """The session's final YOLO count against the current reference.

    confirmed: the last final lot is newer than the last confirmed addition (an addition after it
    requires a new final count) and either matches the reference total or was confirmed by the
    Operator with the difference explicitly acknowledged (single-video flow: there is no second
    capture to retry with). Human validation opens on a confirmed final count.
    valid (= matches): confirmed and equal to the reference total.
    known_mismatch: a physical difference YOLO already reported — the last rejected final count
    (close_blocked) or a confirmed final count that does not match — unless a matching final
    count came after it. Human validation cannot overrule it: it becomes a discrepancy.
    """
    reference = count_reference(session_id)
    state = {"reference": reference, "final": None, "valid": False, "confirmed": False, "matches": False,
             "known_mismatch": None}
    if reference is None:
        return state
    finals = [event for event in _count_events(session_id, "auto_count", family_scoped=False)
              if isinstance(event.payload, dict) and event.payload.get("trigger") == "final"]
    final = finals[-1] if finals else None
    candidates = []
    if final is not None:
        total = int(final.detected_quantity or 0)
        fresh = final.occurred_at > reference["changed_at"]
        state["final"] = {"event_id": final.id, "total": total, "occurred_at": final.occurred_at,
                          "video_sha256": (final.payload.get("video") or {}).get("sha256"),
                          "run_id": final.payload.get("run_id")}
        state["matches"] = state["valid"] = fresh and total == reference["total"]
        state["confirmed"] = state["matches"] or (fresh and bool(final.payload.get("mismatch_acknowledged")))
        if state["confirmed"] and not state["matches"]:
            candidates.append({"event_id": final.id, "final": total, "reference": reference["total"],
                               "occurred_at": final.occurred_at})
    blocked = [event for event in _count_events(session_id, "close_blocked", family_scoped=False)
               if (event.payload or {}).get("reason") == "final_count_mismatch"]
    if blocked:
        last_blocked = blocked[-1]
        candidates.append({"event_id": last_blocked.id, "final": int(last_blocked.detected_quantity or 0),
                           "reference": int(last_blocked.expected_quantity or 0),
                           "occurred_at": last_blocked.occurred_at})
    known = max(candidates, key=lambda item: item["occurred_at"]) if candidates else None
    if known is not None and not (state["matches"] and final.occurred_at > known["occurred_at"]):
        state["known_mismatch"] = known
    return state


def record_vision_checkpoint(work: WorkSession, reason: str, worker_session: dict | None = None) -> str | None:
    """Queue the PEF-DS06 checkpoint of a vision fact already committed in PostgreSQL.

    The outbox (services.mongo_checkpoints) writes MongoDB / EvidenceService in background:
    neither can delay or fail the Operator. The Operator's access token (request context) is
    handed over in memory only, for the evidence upload.
    """
    from flask import g, has_request_context

    from services import mongo_checkpoints
    if not mongo_checkpoints.enabled():
        return None
    try:
        phase = db.session.get(CatOperationPhase, work.current_phase_id) if work.current_phase_id else None
        return mongo_checkpoints.record_checkpoint(
            session_id=work.id, reason=reason, worker_session=worker_session,
            operation_id=work.operation_id, phase_id=work.current_phase_id,
            phase_code=phase.code if phase else None, extended_retention=bool(work.extended_retention),
            access_token=g.get("auth_access_token") if has_request_context() else None,
        )
    except Exception:  # noqa: BLE001 - evidence is never part of the counting transaction
        import logging
        logging.getLogger("pef.checkpoints").exception("checkpoint %s of session %s not queued", reason, work.id)
        return None


def _record_final_count_mismatch(*, session_id, operator_user_id, reference, total, worker_session):
    """Audit trail of a rejected final count (close_blocked); the session stays in progress."""
    # The caller holds the session row lock. A retry of the same rejected run
    # must not create a second incident (including after a portal restart).
    if worker_session.get("run_id") and any(
        (event.payload or {}).get("reason") == "final_count_mismatch"
        and (event.payload or {}).get("run_id") == worker_session["run_id"]
        for event in _count_events(session_id, "close_blocked", family_scoped=False)
    ):
        return
    db.session.add(CountEvent(
        event_type_id=_event_type_id("close_blocked"),
        session_id=session_id,
        user_id=operator_user_id,
        family_id=None,
        expected_quantity=reference["total"],
        detected_quantity=total,
        occurred_at=utc_now(),
        payload={
            "reason": "final_count_mismatch",
            "initial_count_event_id": str(reference["initial"]["event_id"]),
            "initial_total": reference["initial_total"],
            "added_total": reference["added_total"],
            "addition_event_ids": reference["addition_event_ids"],
            "run_id": worker_session.get("run_id"),
            "model_version": worker_session.get("model_version"),
        },
    ))
    try:
        db.session.commit()
    except SQLAlchemyError as exc:
        db.session.rollback()
        raise RfSessionError(N_("Could not persist board auto count."), 503) from exc


def finish_vision_counting(
    *,
    session_id: UUID,
    operator_user_id: UUID,
    worker_session: dict,
) -> dict:
    """Freeze a completed YOLO run: the initial reference count, then the final count.

    The first completed run of the session becomes the initial count (stage "initial"; the
    Operator stays on capture). A later run is the final count: it must come from a new
    capture and its total must equal the initial total, otherwise the attempt is recorded as
    close_blocked and the Operator has to check the tray and capture again. Only a matching
    final count is frozen for the detection summary and human validation (stage "final").
    Totals only: classification is corrected afterwards in human validation.
    """
    _lock_work_session(session_id)
    work = db.session.get(WorkSession, session_id)
    if work is None:
        raise RfSessionError(N_("Session not found."), 404)
    if work.user_id != operator_user_id:
        raise RfSessionError(N_("Session is not assigned to you."), 403)
    status = db.session.get(CatSessionStatus, work.status_id)
    if status is None or status.code != "in_progress" or work.capture_mode != "vision":
        raise RfSessionError(N_("Vision finish is not available for this session."), 409)
    problem = vision_result_problem(worker_session)
    if problem:
        # Nothing is persisted: the session stays in progress so the Operator can retry the
        # upload or switch to the quantity report.
        raise RfSessionError(problem, 409)
    total = vision_capture_total(worker_session)
    if total == 0:
        raise RfSessionError(N_("No instruments were detected in the capture. Check the tray and capture again."), 409)

    counts = worker_session.get("counts") or {}
    run_id = worker_session.get("run_id")
    frames_processed = worker_session.get("frames_processed")
    reference = count_reference(session_id)
    initial = reference["initial"] if reference else None
    if initial is not None and initial["run_id"] == run_id:
        raise RfSessionError(N_("The initial capture cannot be used as the final count. "
                                "Upload the final capture of the tray."), 409)
    if reference is not None and total != reference["total"]:
        _record_final_count_mismatch(session_id=session_id, operator_user_id=operator_user_id,
                                     reference=reference, total=total, worker_session=worker_session)
        record_vision_checkpoint(work, "discrepancy", worker_session)
        if reference["added_total"]:
            raise RfSessionError(N_("Final count %(final)s does not match the reference count %(reference)s "
                                    "(initial %(initial)s + %(added)s added). Check the tray and capture again."),
                                 409, final=total, reference=reference["total"],
                                 initial=reference["initial_total"], added=reference["added_total"])
        raise RfSessionError(N_("Final count %(final)s does not match the initial count %(initial)s. "
                                "Check the tray and capture again."), 409, final=total, initial=reference["total"])

    reason = "detection_initial" if initial is None else "detection_close"
    persist_board_auto_count(
        session_id=session_id,
        operator_user_id=operator_user_id,
        counts=counts,
        model_version=worker_session.get("model_version"),
        reason=reason,
        once_key=f"{session_id}:{reason}:{run_id}:{frames_processed}",
        include_boxes=True,
        capture_total=total,
        out_of_kit=worker_session.get("out_of_kit") or [],
        sample_meta={
            "run_id": run_id,
            "frame_index": worker_session.get("frame_index"),
            "frames_processed": frames_processed,
            "pipeline": worker_session.get("pipeline"),
            "assign_solver": worker_session.get("assign_solver"),
            "model_version": worker_session.get("model_version"),
            "assignment_metrics": worker_session.get("assignment_metrics") or {},
            "conf_threshold": worker_session.get("conf_threshold"),
            "count_sample_every": worker_session.get("count_sample_every"),
        },
    )
    record_vision_checkpoint(work, "start" if initial is None else "close", worker_session)
    if initial is None:
        return {"stage": "initial", "session_id": str(session_id), "initial_total": total}

    snapshot = {
        "stage": "final",
        "session_id": str(session_id),
        "model_version": worker_session.get("model_version"),
        "conf_threshold": worker_session.get("conf_threshold"),
        "pipeline": worker_session.get("pipeline") or "matrix_v1",
        "assign_solver": worker_session.get("assign_solver"),
        "frame_jpeg_b64": worker_session.get("frame_jpeg_b64"),
        "boxes": worker_session.get("boxes") or [],
        "counts": counts,
        "raw_counts": worker_session.get("raw_counts") or {},
        "assignment_metrics": worker_session.get("assignment_metrics") or {},
        "initial_total": reference["initial_total"],
        "added_total": reference["added_total"],
        "reference_total": reference["total"],
        "final_total": total,
        "out_of_kit": worker_session.get("out_of_kit") or [],
        "finished_at": utc_now().isoformat(),
    }
    store_vision_snapshot(session_id, snapshot)
    return snapshot


# --- Single-video flow: initial count from the first frame, final count from the last frame ---------
# One uploaded video. VisionWorker analyses its first valid frame on upload (initial_frame) and,
# after the full run, its last valid frame (final_frame), each with a single-frame inference that
# the intermediate frames never touch. The Operator confirms each edge count explicitly; both lots
# carry the video identity (id + SHA-256) and the frame index, so count, image and video match.


def _vision_session_for_operator(session_id: UUID, operator_user_id: UUID) -> WorkSession:
    """Locked in-progress vision session assigned to the Operator."""
    _lock_work_session(session_id)
    work = db.session.get(WorkSession, session_id)
    if work is None:
        raise RfSessionError(N_("Session not found."), 404)
    if work.user_id != operator_user_id:
        raise RfSessionError(N_("Session is not assigned to you."), 403)
    status = db.session.get(CatSessionStatus, work.status_id)
    if status is None or status.code != "in_progress" or work.capture_mode != "vision":
        raise RfSessionError(N_("Vision finish is not available for this session."), 409)
    return work


def _expected_total(session_id: UUID) -> int:
    return int(db.session.scalar(
        select(func.coalesce(func.sum(ExpectedInventory.expected_quantity), 0))
        .where(ExpectedInventory.session_id == session_id)
    ) or 0)


def _edge_frame_problem(worker_session: dict, position: str) -> tuple[str, dict] | None:
    """Why the worker's edge frame cannot be registered (None = usable). N_ msgid + params."""
    status = worker_session.get("status")
    frame = worker_session.get("initial_frame" if position == "first" else "final_frame") or {}
    if position == "first":
        if status == "loading":
            return N_("The first frame of the video is still being analysed. Wait a moment."), {}
        if not worker_session.get("video_id"):
            return N_("Upload the video of the procedure first."), {}
        if (status == "error" and worker_session.get("error_stage") == "first_frame") or frame.get("ok") is False:
            return (N_("The first frame of the video could not be analysed (%(detail)s). No count was "
                       "generated. Upload the video again, or use the quantity report."),
                    {"detail": frame.get("error") or worker_session.get("last_error") or "error"})
    else:
        if status == "processing":
            return N_("The video is still being processed. Wait until processing finishes."), {}
        if status == "error":
            return (N_("Video processing failed (%(detail)s). No final count was generated. Retry the "
                       "processing, or use the quantity report."),
                    {"detail": frame.get("error") or worker_session.get("last_error") or "error"})
        if status != "ready" or not worker_session.get("run_id"):
            return N_("Play and process the video before confirming the final count."), {}
        if frame.get("run_id") != worker_session.get("run_id"):
            return N_("The last frame does not belong to the current processing run."), {}
    if not frame.get("ok") or frame.get("frame_index") is None:
        return N_("The frame was not analysed. No count was generated."), {}
    if (frame.get("quality") or {}).get("ok") is not True or not frame.get("frame_jpeg_b64"):
        return N_("The frame was not analysed. No count was generated."), {}
    if position == "first" and frame.get("frame_index") != 0:
        return N_("The frame was not analysed. No count was generated."), {}
    if frame.get("video_id") != worker_session.get("video_id"):
        return N_("The analysed frame does not belong to the uploaded video."), {}
    return None


def _edge_lot_payload(*, frame: dict, video: dict, source: str, operator_user_id: UUID, confirmed_at,
                      extra: dict) -> dict:
    return {
        "source": source,
        "video": {"video_id": frame.get("video_id"),
                  **{key: video.get(key) for key in (
                      "sha256", "filename", "size_bytes", "frame_count", "fps", "duration_s")}},
        "frame_index": frame.get("frame_index"),
        "video_t": frame.get("video_t"),
        "frame_quality": frame.get("quality"),
        # Atomic recovery copy: the image survives even if the asynchronous Garage
        # checkpoint cannot be queued after committing this count.
        "frame_jpeg_b64": frame.get("frame_jpeg_b64"),
        "frame_sha256": hashlib.sha256(frame["frame_jpeg_b64"].encode("ascii")).hexdigest(),
        "skipped_frames": [item.get("frame_index") for item in frame.get("skipped_frames") or []],
        "analyzed_at": frame.get("analyzed_at"),
        "confirmed_by_user_id": str(operator_user_id),
        "confirmed_at": confirmed_at.isoformat(),
        **extra,
    }


def _edge_sample_meta(frame: dict, run_id) -> dict:
    return {
        "run_id": run_id,
        "frame_index": frame.get("frame_index"),
        "frames_processed": None,
        "video_t": frame.get("video_t"),
        "pipeline": frame.get("pipeline"),
        "assign_solver": frame.get("assign_solver"),
        "model_version": frame.get("model_version"),
        "assignment_metrics": frame.get("assignment_metrics") or {},
        "conf_threshold": frame.get("conf_threshold"),
    }


def _edge_checkpoint(work: WorkSession, reason: str, frame: dict) -> None:
    """DS06 checkpoint: the analysed edge frame is the key frame (evidence)."""
    record_vision_checkpoint(work, reason, {"boxes": frame.get("boxes") or [],
                                            "frame_jpeg_b64": frame.get("frame_jpeg_b64"),
                                            "model_version": frame.get("model_version")})


def register_initial_count(
    *,
    session_id: UUID,
    operator_user_id: UUID,
    worker_session: dict,
    acknowledge_mismatch: bool = False,
    ip: str | None = None,
) -> dict:
    """Register Initial Count: freeze YOLO's analysis of frame zero of the video.

    The count is that frame's own detections, never a tally of the processed video. It is the
    session's reference count and is immutable: a repeated request for the same video returns the
    registered count (double click), another video is refused. A first frame whose total differs
    from the kit needs the Operator's explicit acknowledgment (no silent false reconciliation).
    """
    work = _vision_session_for_operator(session_id, operator_user_id)
    worker_session = worker_session or {}
    video = worker_session.get("video") or {}
    existing = initial_vision_count(session_id)
    if existing is not None:
        if existing.get("video_sha256") and existing["video_sha256"] == video.get("sha256"):
            return {"stage": "initial", "session_id": str(session_id), "initial_total": existing["total"],
                    "already_registered": True}
        raise RfSessionError(N_("The initial count of this session is already registered and cannot be "
                                "replaced."), 409)
    problem = _edge_frame_problem(worker_session, "first")
    if problem:
        raise RfSessionError(problem[0], 409, **problem[1])
    frame = worker_session["initial_frame"]
    total = len(frame.get("boxes") or [])
    if total == 0:
        raise RfSessionError(N_("No instruments were detected in the first frame. Check that the video "
                                "starts with the full tray in view."), 409)
    expected_total = _expected_total(session_id)
    if total != expected_total and not acknowledge_mismatch:
        raise RfSessionError(N_("The first frame shows %(total)s instruments but the kit expects "
                                "%(expected)s. Check the tray in the image and confirm explicitly to "
                                "register this count."), 409, total=total, expected=expected_total)

    now = utc_now()
    _audit(user_id=operator_user_id, action="session.initial_count", resource_id=session_id,
           institution_id=_session_tenant_id(work), ip=ip)
    written = persist_board_auto_count(
        session_id=session_id,
        operator_user_id=operator_user_id,
        counts=frame.get("counts") or {},
        model_version=frame.get("model_version"),
        reason="detection_initial",
        once_key=f"{session_id}:detection_initial:{frame.get('video_id')}",
        include_boxes=True,
        capture_total=total,
        out_of_kit=frame.get("out_of_kit") or [],
        sample_meta=_edge_sample_meta(frame, None),
        extra_payload=_edge_lot_payload(
            frame=frame, video=video, source="first_frame", operator_user_id=operator_user_id,
            confirmed_at=now, extra={"expected_total": expected_total,
                                     "matches_expected": total == expected_total,
                                     "mismatch_acknowledged": total != expected_total}),
    )
    if not written:
        db.session.rollback()
        raise RfSessionError(N_("The frame was not analysed. No count was generated."), 409)
    _edge_checkpoint(work, "start", frame)
    return {"stage": "initial", "session_id": str(session_id), "initial_total": total,
            "frame_index": frame.get("frame_index"), "expected_total": expected_total}


def confirm_final_count(
    *,
    session_id: UUID,
    operator_user_id: UUID,
    worker_session: dict,
    acknowledge_mismatch: bool = False,
    ip: str | None = None,
) -> dict:
    """Confirm Final Count: freeze YOLO's analysis of the last valid frame of the SAME video.

    Only after the full run finished (worker status ready with its own final_frame). A total that
    differs from the reference (initial + confirmed additions) needs the explicit acknowledgment:
    it is recorded (matches_reference=false) and becomes a known YOLO difference that human
    validation turns into a discrepancy for the Supervisor. Repeating the request for the same run
    returns the confirmed count.
    """
    work = _vision_session_for_operator(session_id, operator_user_id)
    worker_session = worker_session or {}
    reference = count_reference(session_id)
    if reference is None:
        raise RfSessionError(N_("Register the initial count before confirming the final count."), 409)
    initial = reference["initial"]
    if initial.get("source") != "first_frame" or not initial.get("video_sha256"):
        raise RfSessionError(N_("The initial count of this session was not taken from a video frame. "
                                "Use the quantity report to finish the count."), 409)
    state = vision_final_state(session_id)
    run_id = worker_session.get("run_id")
    if state["confirmed"] and run_id and state["final"]["run_id"] == run_id:
        snapshot = get_vision_snapshot(session_id) or {}
        return {**snapshot, "already_confirmed": True}
    problem = _edge_frame_problem(worker_session, "last")
    if problem:
        raise RfSessionError(problem[0], 409, **problem[1])
    video = worker_session.get("video") or {}
    if video.get("sha256") != initial["video_sha256"]:
        raise RfSessionError(N_("The final count must come from the same video as the initial count."), 409)
    frame = worker_session["final_frame"]
    total = len(frame.get("boxes") or [])
    if total == 0:
        raise RfSessionError(N_("No instruments were detected in the last frame. Check that the video "
                                "ends with the tray in view."), 409)
    matches = total == reference["total"]
    if not matches and not acknowledge_mismatch:
        # Recorded like any rejected final count (close_blocked): a later contingency report cannot
        # hide the difference YOLO saw (vision_final_state.known_mismatch).
        _record_final_count_mismatch(session_id=session_id, operator_user_id=operator_user_id,
                                     reference=reference, total=total, worker_session=worker_session)
        _edge_checkpoint(work, "discrepancy", frame)
        raise RfSessionError(N_("The last frame shows %(final)s instruments; the reference count is "
                                "%(reference)s. Check the tray in the image. If the difference is real, "
                                "confirm it explicitly: it will be documented for Supervisor review."),
                             409, final=total, reference=reference["total"])

    now = utc_now()
    _audit(user_id=operator_user_id, action="session.final_count", resource_id=session_id,
           institution_id=_session_tenant_id(work), ip=ip)
    written = persist_board_auto_count(
        session_id=session_id,
        operator_user_id=operator_user_id,
        counts=frame.get("counts") or {},
        model_version=frame.get("model_version"),
        reason="detection_close",
        once_key=f"{session_id}:detection_close:{run_id}",
        include_boxes=True,
        capture_total=total,
        out_of_kit=frame.get("out_of_kit") or [],
        sample_meta=_edge_sample_meta(frame, run_id),
        extra_payload=_edge_lot_payload(
            frame=frame, video=video, source="last_frame", operator_user_id=operator_user_id,
            confirmed_at=now, extra={"initial_count_event_id": str(initial["event_id"]),
                                     "reference": {"total": reference["total"],
                                                   "initial_total": reference["initial_total"],
                                                   "added_total": reference["added_total"]},
                                     "matches_reference": matches, "mismatch_acknowledged": not matches,
                                     "frames_processed": worker_session.get("frames_processed")}),
    )
    if not written:
        db.session.rollback()
        raise RfSessionError(N_("The frame was not analysed. No count was generated."), 409)
    _edge_checkpoint(work, "close" if matches else "discrepancy", frame)
    clear_vision_snapshot(session_id)
    snapshot = get_vision_snapshot(session_id) or {}
    snapshot.update({
        "frame_jpeg_b64": frame.get("frame_jpeg_b64"),
        "boxes": frame.get("boxes") or [],
        "raw_counts": frame.get("raw_counts") or {},
        "counts": frame.get("counts") or snapshot.get("counts") or {},
    })
    store_vision_snapshot(session_id, snapshot)
    return snapshot


def stock_for_families(family_ids: list[UUID], institution_id: UUID | None) -> dict[str, dict]:
    """Simple stock semaphore inputs for SP-02 (only pieces of the caller's institution)."""
    if not family_ids or institution_id is None:
        return {}
    available_status = db.session.scalar(
        select(CatInstrumentCycleStatus).where(CatInstrumentCycleStatus.code == "available")
    )
    result = {}
    for family_id in family_ids:
        total = db.session.scalar(
            select(func.count()).select_from(Instrument).where(
                Instrument.family_id == family_id,
                Instrument.institution_id == institution_id,
                Instrument.active.is_(True),
            )
        ) or 0
        available = 0
        if available_status:
            available = db.session.scalar(
                select(func.count()).select_from(Instrument).where(
                    Instrument.family_id == family_id,
                    Instrument.institution_id == institution_id,
                    Instrument.active.is_(True),
                    Instrument.cycle_status_id == available_status.id,
                )
            ) or 0
        result[str(family_id)] = {"stock_total": int(total), "stock_available": int(available)}
    return result


def semaphore_for_qty(requested: int, stock_available: int) -> str:
    if requested > stock_available:
        return "BLOCK"
    if requested > max(1, int(stock_available * 0.5)):
        return "WARN"
    return "OK"


def _kit_qty_map(kit_id: UUID) -> dict[str, int]:
    rows = db.session.execute(select(KitItem).where(KitItem.kit_id == kit_id)).scalars().all()
    return {str(row.family_id): int(row.quantity) for row in rows}


def _schedule_line_source(family_id: UUID | str, expected_quantity: int, kit_qty_map: dict[str, int]) -> str:
    """kit_snapshot when family is in kit at kit qty; otherwise schedule_additional."""
    key = str(family_id)
    kit_qty = kit_qty_map.get(key)
    if kit_qty is not None and int(kit_qty) == int(expected_quantity):
        return "kit_snapshot"
    return "schedule_additional"


def _normalize_physicians(
    *,
    physician_id: UUID | None,
    physicians: list[dict] | None,
    institution_id: UUID,
) -> list[dict]:
    team = list(physicians or [])
    if not team and physician_id:
        team = [{"physician_id": physician_id, "surgical_role_code": "surgeon"}]
    if not team:
        raise RfSessionError(N_("At least one physician is required."), 400)

    normalized = []
    seen_physicians: set[str] = set()
    for entry in team:
        pid = entry.get("physician_id")
        role_code = (entry.get("surgical_role_code") or "surgeon").strip() or "surgeon"
        if not pid:
            raise RfSessionError(N_("Each surgical team row needs a physician."), 400)
        pid_str = str(pid)
        if pid_str in seen_physicians:
            raise RfSessionError(N_("Each physician can only be selected once."), 400)
        seen_physicians.add(pid_str)
        physician = db.session.get(Physician, UUID(pid_str))
        if physician is None or not physician.active or physician.institution_id != institution_id:
            raise RfSessionError(N_("Choose a valid physician."), 400)
        role = db.session.scalar(select(CatSurgicalRole).where(CatSurgicalRole.code == role_code))
        if role is None:
            raise RfSessionError(f"Invalid surgical role: {role_code}", 400)
        normalized.append({
            "physician_id": UUID(pid_str),
            "surgical_role_id": role.id,
            "surgical_role_code": role.code,
        })
    return normalized


def schedule_session(
    *,
    supervisor_user_id: UUID,
    institution_id: UUID,
    operator_user_id: UUID,
    procedure_type_id: UUID,
    room_id: UUID,
    station_id: UUID,
    kit_id: UUID,
    patient_id: UUID | None,
    physician_id: UUID | None = None,
    physicians: list[dict] | None = None,
    scheduled_at: datetime,
    phase_code: str,
    expected_lines: list[dict],
    ip: str | None = None,
) -> dict:
    """RF-SP-02 save programming (without requiring privacy agreement)."""
    _validate_operator_for_tenant(operator_user_id, institution_id)
    _validate_room_station_for_tenant(room_id, station_id, institution_id)
    _validate_kit_for_tenant(kit_id, institution_id)
    _validate_patient_for_tenant(patient_id, institution_id)

    team = _normalize_physicians(physician_id=physician_id, physicians=physicians, institution_id=institution_id)

    for line in expected_lines:
        qty = int(line["expected_quantity"])
        if qty < 1:
            raise RfSessionError(N_("Expected quantities must be >= 1."), 400)
        stock = stock_for_families([UUID(str(line["family_id"]))], institution_id).get(str(line["family_id"]), {})
        if qty > stock.get("stock_available", 0):
            raise RfSessionError(N_("Stock BLOCK: requested quantity exceeds available instruments."), 400)

    phase = db.session.scalar(select(CatOperationPhase).where(CatOperationPhase.code == phase_code))
    if phase is None:
        raise RfSessionError(N_("Invalid initial phase."), 400)

    operation = Operation(
        scheduled_at=scheduled_at,
        status_id=_operation_status_id("scheduled"),
        procedure_type_id=procedure_type_id,
        room_id=room_id,
        institution_id=institution_id,
    )
    db.session.add(operation)
    db.session.flush()

    if patient_id:
        db.session.add(OperationPatient(operation_id=operation.id, patient_id=patient_id))
    for member in team:
        db.session.add(OperationPhysician(
            operation_id=operation.id,
            physician_id=member["physician_id"],
            surgical_role_id=member["surgical_role_id"],
        ))

    work = WorkSession(
        status_id=_status_id("scheduled"),
        user_id=operator_user_id,
        operation_id=operation.id,
        station_id=station_id,
        kit_id=kit_id,
        current_phase_id=phase.id,
        capture_mode=None,
        atypical_session=False,
        extended_retention=False,
        updated_at=utc_now(),
    )
    db.session.add(work)
    db.session.flush()

    kit_qty_map = _kit_qty_map(kit_id)
    for line in expected_lines:
        family_id = UUID(str(line["family_id"]))
        qty = int(line["expected_quantity"])
        source = _schedule_line_source(family_id, qty, kit_qty_map)
        db.session.add(ExpectedInventory(
            session_id=work.id,
            family_id=family_id,
            expected_quantity=qty,
            source=source,
        ))

    _audit(
        user_id=supervisor_user_id,
        action="session.scheduled",
        resource_id=work.id,
        institution_id=institution_id,
        ip=ip,
    )
    try:
        db.session.commit()
    except SQLAlchemyError as exc:
        db.session.rollback()
        raise RfSessionError(N_("Could not schedule session; please retry."), 503) from exc
    return {"session_id": str(work.id)}


def update_session_inventory(
    *,
    session_id: UUID,
    supervisor_user_id: UUID,
    institution_id: UUID | None,
    lines: list[dict],
    ip: str | None = None,
    physically_added: bool = False,
) -> dict:
    """Upsert expected inventory while session is scheduled or in_progress.

    physically_added (in progress only): the SPD Supervisor confirms that the increases are
    instruments really added to the tray. They are recorded as one instruments_added event
    (per family: previous, new, added) and raise the vision count reference. Without the
    confirmation an edit is administrative and never changes the reference. Resubmitting the
    same quantities adds nothing (only increases over the stored quantity count).
    """
    # Serialise inventory edits of one session so a double submit cannot count twice.
    _lock_work_session(session_id)
    work = _require_session_in_tenant(session_id, institution_id)
    status = db.session.get(CatSessionStatus, work.status_id)
    if status is None or status.code not in {"scheduled", "in_progress"}:
        raise RfSessionError(N_("Inventory can only be modified while scheduled or in progress."), 409)
    if not lines:
        raise RfSessionError(N_("Provide at least one inventory line."), 400)

    kit_qty_map = _kit_qty_map(work.kit_id) if work.kit_id else {}
    existing = {
        str(row.family_id): row
        for row in db.session.execute(
            select(ExpectedInventory).where(ExpectedInventory.session_id == session_id)
        ).scalars()
    }

    previous_total = sum(int(row.expected_quantity) for row in existing.values())
    additions = []
    for line in lines:
        family_id = UUID(str(line["family_id"]))
        qty = int(line["expected_quantity"])
        if qty < 1:
            raise RfSessionError(N_("Expected quantities must be >= 1."), 400)
        before = int(existing[str(family_id)].expected_quantity) if str(family_id) in existing else 0
        if qty > before:
            additions.append({"family_id": str(family_id), "previous": before, "new": qty, "quantity": qty - before})
        stock = stock_for_families([family_id], institution_id).get(str(family_id), {})
        if qty > stock.get("stock_available", 0):
            raise RfSessionError(N_("Stock BLOCK: requested quantity exceeds available instruments."), 400)

        key = str(family_id)
        row = existing.get(key)
        if row is None:
            if status.code == "scheduled" and key not in kit_qty_map:
                source = "schedule_additional"
            elif status.code == "scheduled" and kit_qty_map.get(key) == qty:
                source = "kit_snapshot"
            elif status.code == "scheduled":
                source = "schedule_additional"
            else:
                source = "live_add"
            db.session.add(ExpectedInventory(
                session_id=session_id,
                family_id=family_id,
                expected_quantity=qty,
                source=source,
            ))
            continue

        if int(row.expected_quantity) == qty:
            continue

        if status.code == "scheduled":
            row.source = _schedule_line_source(family_id, qty, kit_qty_map)
        else:
            row.source = "live_add"
        row.expected_quantity = qty

    submitted = {str(line["family_id"]) for line in lines}
    for key, row in existing.items():
        if key not in submitted:
            db.session.delete(row)

    work.updated_at = utc_now()
    added_total = sum(item["quantity"] for item in additions)
    confirmed = physically_added and status.code == "in_progress" and added_total > 0
    if confirmed:
        reference = count_reference(session_id)
        db.session.add(CountEvent(
            event_type_id=_event_type_id("instruments_added"),
            session_id=session_id,
            user_id=supervisor_user_id,
            family_id=None,
            expected_quantity=previous_total,
            detected_quantity=added_total,
            occurred_at=utc_now(),
            payload={
                "added": additions,
                "confirmed_by_role": "spd_supervisor",
                "reference_before": reference["total"] if reference else None,
            },
        ))
    _audit(
        user_id=supervisor_user_id,
        action="session.instruments_added" if confirmed else "session.inventory_updated",
        resource_id=session_id,
        institution_id=institution_id,
        ip=ip,
    )
    try:
        db.session.commit()
    except SQLAlchemyError as exc:
        db.session.rollback()
        raise RfSessionError(N_("Could not update session inventory; please retry."), 503) from exc
    if confirmed:
        # A frozen final count no longer matches the new reference: the Operator captures again.
        clear_vision_snapshot(session_id)
    return {"session_id": str(session_id), "lines": len(lines), "added_total": added_total if confirmed else 0}


def update_scheduled_session(
    *,
    session_id: UUID,
    supervisor_user_id: UUID,
    institution_id: UUID | None,
    operator_user_id: UUID,
    procedure_type_id: UUID,
    room_id: UUID,
    station_id: UUID,
    kit_id: UUID,
    patient_id: UUID | None,
    physicians: list[dict],
    scheduled_at: datetime,
    expected_lines: list[dict],
    ip: str | None = None,
) -> dict:
    """Full edit of a scheduled session (metadata + team + expected inventory)."""
    _lock_work_session(session_id)
    work = _require_session_in_tenant(session_id, institution_id)
    status = db.session.get(CatSessionStatus, work.status_id)
    if status is None or status.code != "scheduled":
        raise RfSessionError(N_("Full session edit is only available while scheduled."), 409)
    operation = db.session.get(Operation, work.operation_id)
    if operation is None:
        raise RfSessionError(N_("Session operation not found."), 404)

    _validate_operator_for_tenant(operator_user_id, institution_id)
    _validate_room_station_for_tenant(room_id, station_id, institution_id)
    _validate_kit_for_tenant(kit_id, institution_id)
    _validate_patient_for_tenant(patient_id, institution_id)

    team = _normalize_physicians(physician_id=None, physicians=physicians, institution_id=institution_id)
    for line in expected_lines:
        qty = int(line["expected_quantity"])
        if qty < 1:
            raise RfSessionError(N_("Expected quantities must be >= 1."), 400)
        stock = stock_for_families([UUID(str(line["family_id"]))], institution_id).get(str(line["family_id"]), {})
        if qty > stock.get("stock_available", 0):
            raise RfSessionError(N_("Stock BLOCK: requested quantity exceeds available instruments."), 400)

    operation.scheduled_at = scheduled_at
    operation.procedure_type_id = procedure_type_id
    operation.room_id = room_id
    work.user_id = operator_user_id
    work.station_id = station_id
    work.kit_id = kit_id
    work.updated_at = utc_now()

    for row in db.session.execute(
        select(OperationPatient).where(OperationPatient.operation_id == operation.id)
    ).scalars():
        db.session.delete(row)
    for row in db.session.execute(
        select(OperationPhysician).where(OperationPhysician.operation_id == operation.id)
    ).scalars():
        db.session.delete(row)
    db.session.flush()

    if patient_id:
        db.session.add(OperationPatient(operation_id=operation.id, patient_id=patient_id))
    for member in team:
        db.session.add(OperationPhysician(
            operation_id=operation.id,
            physician_id=member["physician_id"],
            surgical_role_id=member["surgical_role_id"],
        ))

    kit_qty_map = _kit_qty_map(kit_id)
    existing = {
        str(row.family_id): row
        for row in db.session.execute(
            select(ExpectedInventory).where(ExpectedInventory.session_id == session_id)
        ).scalars()
    }
    submitted = set()
    for line in expected_lines:
        family_id = UUID(str(line["family_id"]))
        qty = int(line["expected_quantity"])
        key = str(family_id)
        submitted.add(key)
        source = _schedule_line_source(family_id, qty, kit_qty_map)
        row = existing.get(key)
        if row is None:
            db.session.add(ExpectedInventory(
                session_id=session_id,
                family_id=family_id,
                expected_quantity=qty,
                source=source,
            ))
        else:
            row.expected_quantity = qty
            row.source = source
    for key, row in existing.items():
        if key not in submitted:
            db.session.delete(row)

    _audit(
        user_id=supervisor_user_id,
        action="session.scheduled_updated",
        resource_id=session_id,
        institution_id=institution_id,
        ip=ip,
    )
    try:
        db.session.commit()
    except SQLAlchemyError as exc:
        db.session.rollback()
        raise RfSessionError(N_("Could not update scheduled session; please retry."), 503) from exc
    return {"session_id": str(session_id)}


def confirm_privacy_via_a(
    *,
    session_id: UUID,
    supervisor_user_id: UUID,
    institution_id: UUID | None,
    privacy_notice_version_id: UUID,
    purpose_model_improvement: bool,
    ip: str | None = None,
) -> dict:
    """RF-SP-02 Via A: confirm existing privacy notice for a scheduled session."""
    _lock_work_session(session_id)
    work = _require_session_in_tenant(session_id, institution_id)
    status = db.session.get(CatSessionStatus, work.status_id)
    if status is None or status.code != "scheduled":
        raise RfSessionError(N_("Privacy agreement can only be set while session is scheduled."), 409)
    if work.capture_mode is not None:
        raise RfSessionError(N_("Capture mode already frozen; cannot change privacy agreement."), 409)

    notice = db.session.get(PrivacyNoticeVersion, privacy_notice_version_id)
    if notice is None or not notice.active:
        raise RfSessionError(N_("Choose an active privacy notice version."), 400)

    existing = db.session.scalar(
        select(SessionProcessingAgreement).where(SessionProcessingAgreement.session_id == session_id)
    )
    now = utc_now()
    if existing:
        existing.privacy_notice_version_id = notice.id
        existing.purpose_quality_ops = True
        existing.purpose_model_improvement = bool(purpose_model_improvement)
        existing.agreed_at = now
        existing.agreed_by_user_id = supervisor_user_id
    else:
        db.session.add(SessionProcessingAgreement(
            session_id=session_id,
            privacy_notice_version_id=notice.id,
            purpose_quality_ops=True,
            purpose_model_improvement=bool(purpose_model_improvement),
            agreed_at=now,
            agreed_by_user_id=supervisor_user_id,
        ))

    _audit(
        user_id=supervisor_user_id,
        action="privacy.session_agreement",
        resource_id=session_id,
        institution_id=institution_id,
        ip=ip,
    )
    try:
        db.session.commit()
    except SQLAlchemyError as exc:
        db.session.rollback()
        raise RfSessionError(N_("Could not confirm privacy notice; please retry."), 503) from exc
    return {"session_id": str(session_id), "privacy_notice_version": notice.version}


def _by_name(options: list[dict]) -> list[dict]:
    """Sort select options by their (localized) display name."""
    return sorted(options, key=lambda option: (option["name"] or "").lower())


def schedule_form_options(institution_id: UUID) -> dict:
    operators = db.session.execute(
        select(User)
        .join(UserRole, UserRole.user_id == User.id)
        .join(Role, Role.id == UserRole.role_id)
        .where(
            Role.code == "station_operator",
            Role.institution_id == institution_id,
            User.active.is_(True),
            User.institution_id == institution_id,
        )
        .order_by(User.name)
    ).scalars().all()
    procedures = db.session.execute(select(CatProcedureType).order_by(CatProcedureType.name)).scalars().all()
    rooms = db.session.execute(
        select(OperatingRoom).where(
            OperatingRoom.institution_id == institution_id,
            OperatingRoom.active.is_(True),
        ).order_by(OperatingRoom.code)
    ).scalars().all()
    stations = db.session.execute(
        select(CaptureStation)
        .join(OperatingRoom, OperatingRoom.id == CaptureStation.room_id)
        .where(CaptureStation.active.is_(True), OperatingRoom.institution_id == institution_id)
        .order_by(CaptureStation.name)
    ).scalars().all()
    kits = db.session.execute(
        select(Kit).where(Kit.institution_id == institution_id, Kit.active.is_(True)).order_by(Kit.name)
    ).scalars().all()
    patients = db.session.execute(
        select(Patient).where(Patient.institution_id == institution_id, Patient.active.is_(True)).order_by(Patient.display_name)
    ).scalars().all()
    physicians = db.session.execute(
        select(Physician).where(Physician.institution_id == institution_id, Physician.active.is_(True)).order_by(Physician.name)
    ).scalars().all()
    surgical_roles = db.session.execute(
        select(CatSurgicalRole).order_by(CatSurgicalRole.name)
    ).scalars().all()
    phases = db.session.execute(
        select(CatOperationPhase).where(CatOperationPhase.active.is_(True)).order_by(CatOperationPhase.name)
    ).scalars().all()
    notices = db.session.execute(
        select(PrivacyNoticeVersion).where(PrivacyNoticeVersion.active.is_(True)).order_by(PrivacyNoticeVersion.version)
    ).scalars().all()
    families = db.session.execute(
        select(InstrumentFamily).where(InstrumentFamily.active.is_(True)).order_by(InstrumentFamily.name)
    ).scalars().all()
    categories = {
        cat.id: cat for cat in db.session.execute(select(CatInstrumentCategory)).scalars()
    }
    purpose_order = [
        "cutting", "dissection", "grasping", "hemostasis", "retraction", "suction", "suturing",
    ]
    family_rows = []
    for family in families:
        category = categories.get(family.category_id)
        code = category.code if category else ""
        rank = purpose_order.index(code) if code in purpose_order else len(purpose_order)
        family_rows.append({
            "id": str(family.id),
            "code": family.code,
            "name": localize_db_text("instrument_family", family.code, family.name),
            "category_code": code,
            "category_label": (localize_db_label("instrument_category", category.code, category.name)
                               if category else gettext("Other")),
            "category_rank": rank,
        })
    family_rows.sort(key=lambda row: (row["category_rank"], row["name"]))
    return {
        "operators": [{"id": str(u.id), "name": u.name} for u in operators],
        # "name" is the display label; "id"/"code" are what the forms submit.
        "procedures": _by_name([
            {"id": str(p.id), "code": p.code, "name": localize_db_label("procedure", p.code, p.name)}
            for p in procedures
        ]),
        "rooms": [
            {"id": str(r.id), "code": r.code, "name": localize_db_text("operating_room", r.code, r.name)}
            for r in rooms
        ],
        "stations": _by_name([
            {"id": str(s.id), "name": localize_db_text("capture_station", s.id, s.name),
             "room_id": str(s.room_id) if s.room_id else ""}
            for s in stations
        ]),
        "kits": _by_name([{"id": str(k.id), "name": localize_db_text("kit", k.id, k.name)} for k in kits]),
        "patients": [{"id": str(p.id), "name": p.display_name} for p in patients],
        "physicians": [{"id": str(p.id), "name": p.name} for p in physicians],
        "surgical_roles": _by_name([
            {"code": r.code, "name": localize_db_label("surgical_role", r.code, r.name)} for r in surgical_roles
        ]),
        "phases": _by_name([
            {"code": p.code, "name": localize_db_label("operation_phase", p.code, p.name)} for p in phases
        ]),
        "privacy_notices": [{
            "id": str(n.id),
            "version": n.version,
            "document_uri": n.document_uri,
            "effective_at": n.effective_at.isoformat() if n.effective_at else "",
        } for n in notices],
        "families": family_rows,
    }


def kit_expected_lines(kit_id: UUID, institution_id: UUID | None) -> list[dict]:
    """Kit lines + stock semaphore; a kit of another institution yields no lines."""
    kit = db.session.get(Kit, kit_id)
    if kit is None or institution_id is None or kit.institution_id != institution_id:
        return []
    rows = db.session.execute(
        select(KitItem, InstrumentFamily)
        .join(InstrumentFamily, InstrumentFamily.id == KitItem.family_id)
        .where(KitItem.kit_id == kit_id)
        .order_by(InstrumentFamily.name)
    ).all()
    categories = {
        cat.id: cat for cat in db.session.execute(select(CatInstrumentCategory)).scalars()
    }
    purpose_order = [
        "cutting", "dissection", "grasping", "hemostasis", "retraction", "suction", "suturing",
    ]
    family_ids = [item.family_id for item, _ in rows]
    stock = stock_for_families(family_ids, institution_id)
    lines = []
    for item, family in rows:
        s = stock.get(str(item.family_id), {"stock_total": 0, "stock_available": 0})
        requested = item.quantity
        category = categories.get(family.category_id)
        code = category.code if category else ""
        rank = purpose_order.index(code) if code in purpose_order else len(purpose_order)
        lines.append({
            "family_id": str(item.family_id),
            "family_name": localize_db_text("instrument_family", family.code, family.name),
            "family_code": family.code,
            "expected_quantity": item.quantity,
            "stock_available": s["stock_available"],
            "stock_total": s["stock_total"],
            "semaphore": semaphore_for_qty(requested, s["stock_available"]),
            "category_code": code,
            "category_label": (localize_db_label("instrument_category", category.code, category.name)
                               if category else gettext("Other")),
            "category_rank": rank,
        })
    lines.sort(key=lambda row: (row["category_rank"], row["family_name"]))
    return lines


def stock_recheck_lines(lines: list[dict], institution_id: UUID | None) -> list[dict]:
    """Recalculate semaphore for arbitrary family/qty pairs (schedule qty edits)."""
    family_ids = [UUID(str(line["family_id"])) for line in lines if line.get("family_id")]
    stock = stock_for_families(family_ids, institution_id)
    result = []
    for line in lines:
        fid = str(line["family_id"])
        qty = int(line.get("expected_quantity") or 0)
        s = stock.get(fid, {"stock_total": 0, "stock_available": 0})
        result.append({
            "family_id": fid,
            "expected_quantity": qty,
            "stock_available": s["stock_available"],
            "stock_total": s["stock_total"],
            "semaphore": semaphore_for_qty(qty, s["stock_available"]),
        })
    return result


def _cycle_status_id(code: str) -> UUID:
    row = db.session.scalar(
        select(CatInstrumentCycleStatus).where(CatInstrumentCycleStatus.code == code)
    )
    if row is None:
        raise RfSessionError(f"Missing cycle status catalog code: {code}", 500)
    return row.id


def resolve_discrepancy(
    *,
    discrepancy_id: UUID,
    supervisor_user_id: UUID,
    institution_id: UUID | None,
    notes: str,
    mark_lost: bool = False,
    mark_recovered: bool = False,
    session_id: UUID | None = None,
    ip: str | None = None,
) -> dict:
    """RF-SP-06 resolve open discrepancy (optionally mark family piece lost or recovered)."""
    if mark_lost and mark_recovered:
        raise RfSessionError(N_("Choose Lost or Recovered, not both."), 400)
    if not (notes or "").strip():
        raise RfSessionError(N_("Resolution notes are required."), 400)

    disc = db.session.get(Discrepancy, discrepancy_id)
    # Same answer for "does not exist", "other session" and "other institution".
    if disc is None or (session_id is not None and disc.session_id != session_id):
        raise RfSessionError(N_("Discrepancy not found."), 404)
    _lock_work_session(disc.session_id)
    db.session.refresh(disc, with_for_update=True)
    work = db.session.get(WorkSession, disc.session_id)
    if work is None or institution_id is None or _session_tenant_id(work) != institution_id:
        raise RfSessionError(N_("Discrepancy not found."), 404)
    status = db.session.get(CatSessionStatus, work.status_id)
    if status is None or status.code not in {"correction_required", "awaiting_spd_review"}:
        # Same rule as SessionService: after the Operator finished and before the SPD close.
        raise RfSessionError(N_("Discrepancies can only be resolved after the Operator finished and before "
                                "the SPD close."), 409)
    if disc.resolved:
        raise RfSessionError(N_("Discrepancy already resolved."), 409)
    reason = db.session.get(CatDiscrepancyReason, disc.reason_id) if disc.reason_id else None
    if reason is not None and reason.code == "shortage" and not (mark_lost or mark_recovered):
        # Same rule as SessionService: a note is not evidence that a missing instrument was found.
        raise RfSessionError(N_("A physically missing instrument cannot be resolved with notes only. "
                                "Record the piece as lost; the incident stays unreconciled."), 409)

    now = utc_now()
    disc.resolved = True
    disc.resolved_at = now
    resolution_note = notes.strip()
    outcome_label = "resolved"
    if mark_lost:
        outcome_label = "marked lost"
    elif mark_recovered:
        outcome_label = "marked recovered"
    disc.description = f"{disc.description}\nSPD resolution ({outcome_label}): {resolution_note}"

    if mark_lost and disc.family_id:
        lost_status = _cycle_status_id("lost")
        piece = db.session.scalar(
            select(Instrument).where(
                Instrument.family_id == disc.family_id,
                Instrument.institution_id == institution_id,
                Instrument.active.is_(True),
            ).limit(1)
        )
        if piece:
            piece.cycle_status_id = lost_status
            piece.active = False
            db.session.add(InstrumentCycleEvent(
                instrument_id=piece.id,
                cycle_status_id=lost_status,
                occurred_at=now,
                session_id=work.id,
                operation_id=work.operation_id,
                notes=resolution_note or "Marked lost during SPD discrepancy review",
            ))
            active_res = db.session.execute(
                select(InstrumentReservation).where(
                    InstrumentReservation.instrument_id == piece.id,
                    InstrumentReservation.active.is_(True),
                )
            ).scalars().all()
            for reservation in active_res:
                reservation.active = False
                reservation.released_at = now

    if mark_recovered and disc.family_id:
        lost_status = _cycle_status_id("lost")
        available_status = _cycle_status_id("available")
        piece = db.session.scalar(
            select(Instrument).where(
                Instrument.family_id == disc.family_id,
                Instrument.institution_id == institution_id,
                Instrument.cycle_status_id == lost_status,
            ).limit(1)
        )
        if piece:
            piece.cycle_status_id = available_status
            piece.active = True
            db.session.add(InstrumentCycleEvent(
                instrument_id=piece.id,
                cycle_status_id=available_status,
                occurred_at=now,
                session_id=work.id,
                operation_id=work.operation_id,
                notes=resolution_note or "Recovered during SPD discrepancy review",
            ))
        else:
            disc.description = (
                f"{disc.description}\n"
                "Note: no lost piece of this family was found to reactivate."
            )

    open_left = db.session.scalar(
        select(func.count()).select_from(Discrepancy).where(
            Discrepancy.session_id == work.id,
            Discrepancy.resolved.is_(False),
            Discrepancy.id != disc.id,
        )
    ) or 0
    # count current disc as resolved in same flush
    still_open = int(open_left)
    if still_open == 0:
        status = db.session.get(CatSessionStatus, work.status_id)
        if status and status.code == "correction_required":
            work.status_id = _status_id("awaiting_spd_review")
            work.updated_at = now

    _audit(
        user_id=supervisor_user_id,
        action="discrepancy.resolve",
        resource_id=work.id,
        institution_id=institution_id,
        ip=ip,
    )
    try:
        db.session.commit()
    except SQLAlchemyError as exc:
        db.session.rollback()
        raise RfSessionError(N_("Could not resolve discrepancy; please retry."), 503) from exc
    return {
        "session_id": str(work.id),
        "discrepancy_id": str(discrepancy_id),
        "open_remaining": still_open,
        "session_status": db.session.get(CatSessionStatus, work.status_id).code,
    }


def confirm_spd_close(
    *,
    session_id: UUID,
    supervisor_user_id: UUID,
    institution_id: UUID | None,
    material_recovered: bool,
    ip: str | None = None,
    contingency_reviewed: bool = False,
    unreconciled_shortage_acknowledged: bool = False,
) -> dict:
    """RF-SP-05 safe close: SPD confirmation once the Operator finished and nothing is open.

    A physically missing instrument (discrepancy reason `shortage`) is never a safe close: the session
    is closed only with the explicit acknowledgment and recorded safe_close_confirmed = false (same
    rule as SessionService).

    A contingency count (manual report instead of YOLO) also needs the SPD to confirm they
    reviewed it."""
    if not material_recovered:
        raise RfSessionError(N_("Confirm material recovered / reprocessing started."), 400)
    _lock_work_session(session_id)
    work = _require_session_in_tenant(session_id, institution_id)
    status = db.session.get(CatSessionStatus, work.status_id)
    if status is None or status.code != "awaiting_spd_review":
        raise RfSessionError(N_("Session is not awaiting SPD review."), 409)
    open_count = db.session.scalar(
        select(func.count()).select_from(Discrepancy).where(
            Discrepancy.session_id == session_id,
            Discrepancy.resolved.is_(False),
        )
    ) or 0
    if open_count:
        raise RfSessionError(N_("Resolve open discrepancies before closing."), 409)
    contingency = contingency_for_session(session_id)
    if contingency and not contingency_reviewed:
        raise RfSessionError(N_("Confirm that you reviewed the contingency count before closing."), 400)
    shortages = [str(d) for d in db.session.scalars(
        select(Discrepancy.id).join(CatDiscrepancyReason, CatDiscrepancyReason.id == Discrepancy.reason_id)
        .where(Discrepancy.session_id == session_id, CatDiscrepancyReason.code == "shortage")
        .order_by(Discrepancy.id)
    ).all()]
    if shortages and not unreconciled_shortage_acknowledged:
        raise RfSessionError(N_("This session has a physically missing instrument that was not documented "
                                "as recovered. Acknowledge that it is closed as an unreconciled shortage."), 409)

    _close_work_session(
        work, closer_user_id=supervisor_user_id, institution_id=institution_id,
        payload={"closed_by": "spd_supervisor", "material_recovered": True, "safe_close_confirmed": not shortages,
                 "contingency_reviewed": bool(contingency), "unreconciled_physical_shortages": shortages},
        cycle_notes="SPD confirm close — reprocessing started",
    )
    _audit(
        user_id=supervisor_user_id,
        action="session.spd_close",
        resource_id=session_id,
        institution_id=institution_id,
        ip=ip,
    )
    try:
        db.session.commit()
    except SQLAlchemyError as exc:
        db.session.rollback()
        raise RfSessionError(N_("Could not close session; please retry."), 503) from exc
    return {"session_id": str(session_id), "status_code": "closed"}


def _close_work_session(work: WorkSession, *, closer_user_id: UUID, institution_id: UUID, payload: dict,
                        cycle_notes: str) -> None:
    """Close effects shared by the SPD close and the Operator close (caller holds the row lock).

    closed + closed_by + ended_at (if missing); the operation's active reservations of the session's
    institution are released and their pieces go to sterilization; one session_close count event.
    """
    now = utc_now()
    work.status_id = _status_id("closed")
    work.closed_by_user_id = closer_user_id
    work.updated_at = now
    if work.ended_at is None:
        work.ended_at = now

    sterilizing = _cycle_status_id("sterilization")
    if work.operation_id:
        # Only this session's operation, and only pieces of the session's institution.
        reservations = db.session.execute(
            select(InstrumentReservation)
            .join(Instrument, Instrument.id == InstrumentReservation.instrument_id)
            .where(
                InstrumentReservation.operation_id == work.operation_id,
                InstrumentReservation.active.is_(True),
                Instrument.institution_id == institution_id,
            )
        ).scalars().all()
        for reservation in reservations:
            reservation.active = False
            reservation.released_at = now
            instrument = db.session.get(Instrument, reservation.instrument_id)
            if instrument:
                instrument.cycle_status_id = sterilizing
                db.session.add(InstrumentCycleEvent(
                    instrument_id=instrument.id,
                    cycle_status_id=sterilizing,
                    occurred_at=now,
                    session_id=work.id,
                    operation_id=work.operation_id,
                    notes=cycle_notes,
                ))

    db.session.add(CountEvent(
        event_type_id=_event_type_id("session_close"),
        session_id=work.id,
        user_id=closer_user_id,
        occurred_at=now,
        payload=payload,
    ))


# --- Operator close (no discrepancies) ----------------------------------------------------------------
# The Operator closes a vision session directly when the YOLO edge counts and the human validation are
# reconciled family by family and nothing is open or was ever raised. Anything else stays with the SPD
# Supervisor (awaiting_spd_review / correction_required). SessionService applies the same rules
# (services/session_operator_close.py); this is the in-process fallback without SESSION_SERVICE_URL.

OPERATOR_CLOSE_BLOCKERS = {
    "evidence_integrity": N_("Endpoint frame evidence is incomplete or inconsistent. Supervisor review required."),
    "initial_family_mismatch": N_("Initial quantities do not match the kit family by family. Supervisor review required."),
    "not_awaiting_review": N_("Human validation has not been submitted, or the session is no longer in review."),
    "not_vision": N_("Only vision sessions can be closed by the Operator."),
    "not_vision_validation": N_("The count was not finished through YOLO human validation."),
    "contingency": N_("A contingency count (quantity report) needs Supervisor review."),
    "discrepancies_recorded": N_("Discrepancies were recorded for this session."),
    "extra_instruments": N_("Instruments outside the kit were confirmed on the tray."),
    "out_of_kit_detected": N_("YOLO detected instruments outside the kit."),
    "final_count_mismatch": N_("The final count differs from the reference count."),
    "count_blocked": N_("A final count was rejected during the session."),
    "missing_initial": N_("No initial count from the first video frame."),
    "missing_final": N_("No final count from the last video frame."),
    "different_video": N_("The initial and final counts do not come from the same video."),
    "initial_final_mismatch": N_("Initial and final totals do not match."),
    "expected_total_mismatch": N_("The initial count differs from the instruments required by the kit."),
    "family_mismatch": N_("Validated quantities do not match the kit family by family."),
    "validated_total_mismatch": N_("The validated total differs from the YOLO count."),
}


def _latest_human_counts(session_id: UUID) -> dict[str, int]:
    human = {}
    for event in _count_events(session_id, "manual_count"):
        if event.family_id is not None:
            human[str(event.family_id)] = int(event.detected_quantity or 0)
    return human


def operator_close_eligibility(session_id: UUID) -> dict:
    """Whether the assigned Operator may close the session (reasons = blocker codes, summary for the UI)."""
    work = db.session.get(WorkSession, session_id)
    status = db.session.get(CatSessionStatus, work.status_id) if work else None
    reasons = []
    if status is None or status.code != "awaiting_spd_review":
        reasons.append("not_awaiting_review")
    if work is None or work.capture_mode != "vision":
        reasons.append("not_vision")
    closes = _count_events(session_id, "manual_close")
    report = closes[-1].payload if closes and isinstance(closes[-1].payload, dict) else {}
    if report.get("operator_close_path") != "vision_validation":
        reasons.append("not_vision_validation")
    if report.get("contingency"):
        reasons.append("contingency")
    if report.get("extra_instruments"):
        reasons.append("extra_instruments")
    if report.get("known_final_mismatch"):
        reasons.append("final_count_mismatch")
    discrepancies = db.session.scalar(
        select(func.count()).select_from(Discrepancy).where(Discrepancy.session_id == session_id)) or 0
    if discrepancies:
        reasons.append("discrepancies_recorded")
    if _count_events(session_id, "close_blocked"):
        reasons.append("count_blocked")

    reference = count_reference(session_id)
    state = vision_final_state(session_id)
    initial = reference["initial"] if reference else None
    final = state["final"]
    if initial is None or initial.get("source") != "first_frame" or not initial.get("video_sha256"):
        reasons.append("missing_initial")
    if final is None or not state["confirmed"]:
        reasons.append("missing_final")
    elif initial is not None and final.get("video_sha256") != initial.get("video_sha256"):
        reasons.append("different_video")
    if reference is not None and final is not None and not state["matches"]:
        reasons.append("initial_final_mismatch")
    for event_id in filter(None, [initial and initial["event_id"], final and final["event_id"]]):
        lot = db.session.get(CountEvent, event_id)
        if lot is not None and (lot.payload or {}).get("out_of_kit"):
            reasons.append("out_of_kit_detected")
            break

    expected = {str(row.family_id): int(row.expected_quantity) for row in db.session.execute(
        select(ExpectedInventory).where(ExpectedInventory.session_id == session_id)).scalars()}
    human = _latest_human_counts(session_id)
    expected_total = sum(expected.values())
    validated_total = sum(human.get(family_id, 0) for family_id in expected)
    initial_event = db.session.get(CountEvent, initial["event_id"]) if initial else None
    initial_payload = (initial_event.payload or {}) if initial_event else {}
    final_event = db.session.get(CountEvent, final["event_id"]) if final else None
    for event in (initial_event, final_event):
        payload = (event.payload or {}) if event else {}
        image = payload.get("frame_jpeg_b64")
        if not isinstance(image, str) or not image or hashlib.sha256(image.encode("utf-8")).hexdigest() != payload.get("frame_sha256"):
            reasons.append("evidence_integrity")
    initial_families = {str(row.get("family_id")): int(row.get("detected_quantity") or 0)
                        for row in (initial_payload.get("board") or {}).get("families", [])}
    if initial_families != expected:
        reasons.append("initial_family_mismatch")
    if any(qty and family_id not in expected for family_id, qty in human.items()):
        reasons.append("extra_instruments")
    if not expected or any(human.get(family_id) != qty for family_id, qty in expected.items()):
        reasons.append("family_mismatch")
    if reference is not None and expected_total != reference["total"]:
        reasons.append("expected_total_mismatch")
    if final is not None and validated_total != final["total"]:
        reasons.append("validated_total_mismatch")
    reasons = list(dict.fromkeys(reasons))
    return {
        "eligible": not reasons,
        "reasons": reasons,
        "summary": {
            "expected_total": expected_total,
            "initial_total": reference["initial_total"] if reference else None,
            "added_total": reference["added_total"] if reference else None,
            "reference_total": reference["total"] if reference else None,
            "final_total": final["total"] if final else None,
            "validated_total": validated_total,
            "discrepancies": int(discrepancies),
            "families": [{"family_id": family_id, "expected": qty, "validated": human.get(family_id)}
                         for family_id, qty in expected.items()],
        },
    }


def operator_close_session(
    *,
    session_id: UUID,
    operator_user_id: UUID,
    institution_id: UUID | None,
    physical_integrity_confirmed: bool,
    material_recovered: bool,
    ip: str | None = None,
) -> dict:
    """Operator close of a reconciled vision session: closed, safe_close_confirmed = true.

    Every condition is re-evaluated under the work_session row lock (the UI is never trusted). A
    repeated request after the same Operator closed it answers the closed state again (no second
    close event); any other closed session answers 409.
    """
    if not physical_integrity_confirmed:
        raise RfSessionError(N_("Confirm that the physical instrument set is complete."), 400)
    if not material_recovered:
        raise RfSessionError(N_("Confirm material recovered / reprocessing started."), 400)
    _lock_work_session(session_id)
    work = _require_session_in_tenant(session_id, institution_id)
    if work.user_id != operator_user_id:
        raise RfSessionError(N_("Session is not assigned to you."), 403)
    status = db.session.get(CatSessionStatus, work.status_id)
    if status is not None and status.code == "closed":
        closes = _count_events(session_id, "session_close")
        last = closes[-1].payload if closes and isinstance(closes[-1].payload, dict) else {}
        if work.closed_by_user_id == operator_user_id and last.get("closed_by") == "station_operator":
            return {"session_id": str(session_id), "status_code": "closed", "already_closed": True}
        raise RfSessionError(N_("Session is already closed."), 409)
    eligibility = operator_close_eligibility(session_id)
    if not eligibility["eligible"]:
        error = RfSessionError(N_("This session does not meet the conditions for an Operator close. It stays "
                                  "in Supervisor review."), 409)
        error.reasons = eligibility["reasons"]
        raise error

    _close_work_session(
        work, closer_user_id=operator_user_id, institution_id=institution_id,
        payload={"closed_by": "station_operator", "operator_close": True, "material_recovered": True,
                 "physical_integrity_confirmed": True, "safe_close_confirmed": True,
                 "contingency_reviewed": False, "unreconciled_physical_shortages": [],
                 "reconciliation": {key: value for key, value in eligibility["summary"].items() if key != "families"}},
        cycle_notes="Operator close — reprocessing started",
    )
    _audit(user_id=operator_user_id, action="session.operator_close", resource_id=session_id,
           institution_id=institution_id, ip=ip)
    try:
        db.session.commit()
    except SQLAlchemyError as exc:
        db.session.rollback()
        raise RfSessionError(N_("Could not close session; please retry."), 503) from exc
    clear_vision_snapshot(session_id)
    return {"session_id": str(session_id), "status_code": "closed"}
