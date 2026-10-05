"""RF session write paths: Start (OP-03), manual close (OP-06M), SPD schedule/privacy (SP-02)."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError

from extensions import db
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
    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.message = message
        self.status_code = status_code


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


def start_session(*, session_id: UUID, operator_user_id: UUID, institution_id: UUID | None, ip: str | None = None) -> dict:
    """RF-OP-03 Start Session transaction (no evidence/WSS in this stage)."""
    work = db.session.get(WorkSession, session_id)
    if work is None:
        raise RfSessionError("Session not found.", 404)
    if work.user_id != operator_user_id:
        raise RfSessionError("Session is not assigned to you.", 403)
    status = db.session.get(CatSessionStatus, work.status_id)
    if status is None or status.code != "scheduled":
        raise RfSessionError("Session is no longer scheduled.", 409)

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
        raise RfSessionError("Could not start session; please retry.", 503) from exc

    if capture_mode == "vision":
        try:
            from services.vision_bridge import VisionBridgeError, bind_session as vision_bind
            vision_bind(session_id)
        except Exception:
            # Worker may be down; OP capture UI shows offline banner and can retry bind.
            pass

    return {"capture_mode": capture_mode, "session_id": str(session_id)}


def submit_manual_close(
    *,
    session_id: UUID,
    operator_user_id: UUID,
    institution_id: UUID | None,
    reports: list[dict],
    ip: str | None = None,
) -> dict:
    """RF-OP-06M Submit report and close counting (manual path)."""
    work = db.session.get(WorkSession, session_id)
    if work is None:
        raise RfSessionError("Session not found.", 404)
    if work.user_id != operator_user_id:
        raise RfSessionError("Session is not assigned to you.", 403)
    status = db.session.get(CatSessionStatus, work.status_id)
    if status is None or status.code != "in_progress":
        raise RfSessionError("Session is not in progress.", 409)
    # Vision sessions may use the quantity-report path until the video/YOLO feed is wired.
    if work.capture_mode not in {"manual_no_privacy", "vision"}:
        raise RfSessionError("Quantity report is not allowed for this capture mode.", 409)

    expected_rows = db.session.execute(
        select(ExpectedInventory).where(ExpectedInventory.session_id == session_id)
    ).scalars().all()
    if not expected_rows:
        raise RfSessionError("Session has no expected inventory.", 409)

    by_family = {str(item.family_id): item for item in expected_rows}
    if set(by_family) != {str(r["family_id"]) for r in reports}:
        raise RfSessionError("Report must include every expected family exactly once.", 400)

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
            raise RfSessionError("Reported quantity must be >= 0.", 400)
        reason_code = (report.get("reason_code") or "").strip()
        notes = (report.get("notes") or "").strip()
        diff = reported - expected.expected_quantity
        if diff != 0 and not reason_code:
            raise RfSessionError("Reason is required when reported quantity differs from expected.", 400)
        if reason_code == "other" and len(notes) < 10:
            raise RfSessionError("Notes must have at least 10 characters when reason is Other.", 400)

        event = CountEvent(
            event_type_id=manual_count_type,
            session_id=session_id,
            user_id=operator_user_id,
            family_id=family_id,
            expected_quantity=expected.expected_quantity,
            detected_quantity=None,
            occurred_at=now,
            payload={
                "ai": work.capture_mode == "vision",
                "capture_mode": work.capture_mode or "manual_no_privacy",
                "reported_quantity": reported,
                "reason_code": reason_code or None,
                "notes": notes or None,
                "close_path": "manual_count",
            },
        )
        db.session.add(event)
        db.session.flush()

        if diff != 0:
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
        },
    ))
    _audit(
        user_id=operator_user_id,
        action="session.manual_close",
        resource_id=session_id,
        institution_id=institution_id,
        ip=ip,
    )
    try:
        db.session.commit()
    except SQLAlchemyError as exc:
        db.session.rollback()
        raise RfSessionError("Could not submit report; please retry.", 503) from exc

    if capture_mode == "vision":
        try:
            from services.vision_bridge import unbind_session as vision_unbind
            vision_unbind(session_id)
        except Exception:
            pass
        clear_vision_snapshot(session_id)

    return {
        "session_id": str(session_id),
        "status_code": next_status,
        "open_discrepancies": open_discrepancies,
    }


# In-process snapshot for RF-OP-05/06 after Finish counting (no Mongo this cycle).
_VISION_SNAPSHOTS: dict[str, dict] = {}
_AUTO_COUNT_FLUSHED: set[str] = set()


def store_vision_snapshot(session_id: UUID | str, snapshot: dict) -> None:
    _VISION_SNAPSHOTS[str(session_id)] = snapshot


def get_vision_snapshot(session_id: UUID | str) -> dict | None:
    return _VISION_SNAPSHOTS.get(str(session_id))


def clear_vision_snapshot(session_id: UUID | str) -> None:
    sid = str(session_id)
    _VISION_SNAPSHOTS.pop(sid, None)
    stale = [key for key in _AUTO_COUNT_FLUSHED if key.startswith(f"{sid}:")]
    for key in stale:
        _AUTO_COUNT_FLUSHED.discard(key)


def change_phase_stub(
    *,
    session_id: UUID,
    operator_user_id: UUID,
    institution_id: UUID | None,
    to_phase_code: str,
    ip: str | None = None,
) -> dict:
    """Real phase UPDATE + count_event; no Mongo/Garage side effects in this demo cycle."""
    work = db.session.get(WorkSession, session_id)
    if work is None:
        raise RfSessionError("Session not found.", 404)
    if work.user_id != operator_user_id:
        raise RfSessionError("Session is not assigned to you.", 403)
    status = db.session.get(CatSessionStatus, work.status_id)
    if status is None or status.code != "in_progress":
        raise RfSessionError("Session is not in progress.", 409)
    to_phase = db.session.scalar(select(CatOperationPhase).where(CatOperationPhase.code == to_phase_code))
    if to_phase is None:
        raise RfSessionError("Unknown phase code.", 400)
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
        raise RfSessionError("Could not change phase; please retry.", 503) from exc
    return {
        "session_id": str(session_id),
        "phase_code": to_phase.code,
        "phase_name": to_phase.name,
    }


def persist_auto_counts_from_worker(
    *,
    session_id: UUID,
    operator_user_id: UUID,
    counts: dict,
    model_version: str | None = None,
    reason: str = "video_window",
    once_key: str | None = None,
) -> int:
    """Insert auto_count events for worker tallies (one row per family with a detection or finish)."""
    flush_key = once_key or f"{session_id}:{reason}"
    if flush_key in _AUTO_COUNT_FLUSHED and reason != "detection_close":
        return 0
    work = db.session.get(WorkSession, session_id)
    if work is None:
        raise RfSessionError("Session not found.", 404)
    expected_rows = {
        str(row.family_id): row
        for row in db.session.execute(
            select(ExpectedInventory).where(ExpectedInventory.session_id == session_id)
        ).scalars().all()
    }
    now = utc_now()
    auto_type = _event_type_id("auto_count")
    written = 0
    for _code, meta in (counts or {}).items():
        family_id = meta.get("family_id")
        if not family_id or str(family_id) not in expected_rows:
            continue
        exp = expected_rows[str(family_id)]
        detected = int(meta.get("detected_quantity") or 0)
        db.session.add(CountEvent(
            event_type_id=auto_type,
            session_id=session_id,
            user_id=operator_user_id,
            family_id=UUID(str(family_id)),
            expected_quantity=exp.expected_quantity,
            detected_quantity=detected,
            occurred_at=now,
            payload={
                "ai": True,
                "capture_mode": "vision",
                "avg_confidence": meta.get("avg_confidence"),
                "model_version": model_version,
                "reason": reason,
            },
        ))
        written += 1
    if written:
        try:
            db.session.commit()
        except SQLAlchemyError as exc:
            db.session.rollback()
            raise RfSessionError("Could not persist auto counts.", 503) from exc
        _AUTO_COUNT_FLUSHED.add(flush_key)
    return written


def finish_vision_counting(
    *,
    session_id: UUID,
    operator_user_id: UUID,
    worker_session: dict,
) -> dict:
    """Freeze worker snapshot for OP-05 and write closing auto_count rows."""
    work = db.session.get(WorkSession, session_id)
    if work is None:
        raise RfSessionError("Session not found.", 404)
    if work.user_id != operator_user_id:
        raise RfSessionError("Session is not assigned to you.", 403)
    status = db.session.get(CatSessionStatus, work.status_id)
    if status is None or status.code != "in_progress" or work.capture_mode != "vision":
        raise RfSessionError("Vision finish is not available for this session.", 409)

    counts = worker_session.get("counts") or {}
    persist_auto_counts_from_worker(
        session_id=session_id,
        operator_user_id=operator_user_id,
        counts=counts,
        model_version=worker_session.get("model_version"),
        reason="detection_close",
    )
    snapshot = {
        "session_id": str(session_id),
        "model_version": worker_session.get("model_version"),
        "conf_threshold": worker_session.get("conf_threshold"),
        "frame_jpeg_b64": worker_session.get("frame_jpeg_b64"),
        "boxes": worker_session.get("boxes") or [],
        "counts": counts,
        "finished_at": utc_now().isoformat(),
    }
    store_vision_snapshot(session_id, snapshot)
    return snapshot


def stock_for_families(family_ids: list[UUID]) -> dict[str, dict]:
    """Simple stock semaphore inputs for SP-02."""
    if not family_ids:
        return {}
    available_status = db.session.scalar(
        select(CatInstrumentCycleStatus).where(CatInstrumentCycleStatus.code == "available")
    )
    result = {}
    for family_id in family_ids:
        total = db.session.scalar(
            select(func.count()).select_from(Instrument).where(
                Instrument.family_id == family_id,
                Instrument.active.is_(True),
            )
        ) or 0
        available = 0
        if available_status:
            available = db.session.scalar(
                select(func.count()).select_from(Instrument).where(
                    Instrument.family_id == family_id,
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
) -> list[dict]:
    team = list(physicians or [])
    if not team and physician_id:
        team = [{"physician_id": physician_id, "surgical_role_code": "surgeon"}]
    if not team:
        raise RfSessionError("At least one physician is required.", 400)

    normalized = []
    seen_physicians: set[str] = set()
    for entry in team:
        pid = entry.get("physician_id")
        role_code = (entry.get("surgical_role_code") or "surgeon").strip() or "surgeon"
        if not pid:
            raise RfSessionError("Each surgical team row needs a physician.", 400)
        pid_str = str(pid)
        if pid_str in seen_physicians:
            raise RfSessionError("Each physician can only be selected once.", 400)
        seen_physicians.add(pid_str)
        physician = db.session.get(Physician, UUID(pid_str))
        if physician is None or not physician.active:
            raise RfSessionError("Choose a valid physician.", 400)
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
    operator = db.session.get(User, operator_user_id)
    if operator is None or not operator.active:
        raise RfSessionError("Choose a valid operator.", 400)
    role_ok = db.session.scalar(
        select(UserRole)
        .join(Role, Role.id == UserRole.role_id)
        .where(UserRole.user_id == operator_user_id, Role.code == "station_operator")
    )
    if role_ok is None:
        raise RfSessionError("Assigned user must have station_operator role.", 400)

    team = _normalize_physicians(physician_id=physician_id, physicians=physicians)

    for line in expected_lines:
        qty = int(line["expected_quantity"])
        if qty < 1:
            raise RfSessionError("Expected quantities must be >= 1.", 400)
        stock = stock_for_families([UUID(str(line["family_id"]))]).get(str(line["family_id"]), {})
        if qty > stock.get("stock_available", 0):
            raise RfSessionError("Stock BLOCK: requested quantity exceeds available instruments.", 400)

    phase = db.session.scalar(select(CatOperationPhase).where(CatOperationPhase.code == phase_code))
    if phase is None:
        raise RfSessionError("Invalid initial phase.", 400)

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
        raise RfSessionError("Could not schedule session; please retry.", 503) from exc
    return {"session_id": str(work.id)}


def update_session_inventory(
    *,
    session_id: UUID,
    supervisor_user_id: UUID,
    institution_id: UUID | None,
    lines: list[dict],
    ip: str | None = None,
) -> dict:
    """Upsert expected inventory while session is scheduled or in_progress."""
    work = db.session.get(WorkSession, session_id)
    if work is None:
        raise RfSessionError("Session not found.", 404)
    status = db.session.get(CatSessionStatus, work.status_id)
    if status is None or status.code not in {"scheduled", "in_progress"}:
        raise RfSessionError("Inventory can only be modified while scheduled or in progress.", 409)
    if not lines:
        raise RfSessionError("Provide at least one inventory line.", 400)

    kit_qty_map = _kit_qty_map(work.kit_id) if work.kit_id else {}
    existing = {
        str(row.family_id): row
        for row in db.session.execute(
            select(ExpectedInventory).where(ExpectedInventory.session_id == session_id)
        ).scalars()
    }

    for line in lines:
        family_id = UUID(str(line["family_id"]))
        qty = int(line["expected_quantity"])
        if qty < 1:
            raise RfSessionError("Expected quantities must be >= 1.", 400)
        stock = stock_for_families([family_id]).get(str(family_id), {})
        if qty > stock.get("stock_available", 0):
            raise RfSessionError("Stock BLOCK: requested quantity exceeds available instruments.", 400)

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
    _audit(
        user_id=supervisor_user_id,
        action="session.inventory_updated",
        resource_id=session_id,
        institution_id=institution_id,
        ip=ip,
    )
    try:
        db.session.commit()
    except SQLAlchemyError as exc:
        db.session.rollback()
        raise RfSessionError("Could not update session inventory; please retry.", 503) from exc
    return {"session_id": str(session_id), "lines": len(lines)}


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
    work = db.session.get(WorkSession, session_id)
    if work is None:
        raise RfSessionError("Session not found.", 404)
    status = db.session.get(CatSessionStatus, work.status_id)
    if status is None or status.code != "scheduled":
        raise RfSessionError("Full session edit is only available while scheduled.", 409)
    operation = db.session.get(Operation, work.operation_id)
    if operation is None:
        raise RfSessionError("Session operation not found.", 404)

    operator = db.session.get(User, operator_user_id)
    if operator is None or not operator.active:
        raise RfSessionError("Choose a valid operator.", 400)
    role_ok = db.session.scalar(
        select(UserRole)
        .join(Role, Role.id == UserRole.role_id)
        .where(UserRole.user_id == operator_user_id, Role.code == "station_operator")
    )
    if role_ok is None:
        raise RfSessionError("Assigned user must have station_operator role.", 400)

    team = _normalize_physicians(physician_id=None, physicians=physicians)
    for line in expected_lines:
        qty = int(line["expected_quantity"])
        if qty < 1:
            raise RfSessionError("Expected quantities must be >= 1.", 400)
        stock = stock_for_families([UUID(str(line["family_id"]))]).get(str(line["family_id"]), {})
        if qty > stock.get("stock_available", 0):
            raise RfSessionError("Stock BLOCK: requested quantity exceeds available instruments.", 400)

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
        raise RfSessionError("Could not update scheduled session; please retry.", 503) from exc
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
    work = db.session.get(WorkSession, session_id)
    if work is None:
        raise RfSessionError("Session not found.", 404)
    status = db.session.get(CatSessionStatus, work.status_id)
    if status is None or status.code != "scheduled":
        raise RfSessionError("Privacy agreement can only be set while session is scheduled.", 409)
    if work.capture_mode is not None:
        raise RfSessionError("Capture mode already frozen; cannot change privacy agreement.", 409)

    notice = db.session.get(PrivacyNoticeVersion, privacy_notice_version_id)
    if notice is None or not notice.active:
        raise RfSessionError("Choose an active privacy notice version.", 400)

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
        raise RfSessionError("Could not confirm privacy notice; please retry.", 503) from exc
    return {"session_id": str(session_id), "privacy_notice_version": notice.version}


def schedule_form_options(institution_id: UUID) -> dict:
    operators = db.session.execute(
        select(User)
        .join(UserRole, UserRole.user_id == User.id)
        .join(Role, Role.id == UserRole.role_id)
        .where(Role.code == "station_operator", User.active.is_(True), User.institution_id == institution_id)
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
        select(CaptureStation).where(CaptureStation.active.is_(True)).order_by(CaptureStation.name)
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
            "name": family.name,
            "category_code": code,
            "category_label": category.name if category else "Other",
            "category_rank": rank,
        })
    family_rows.sort(key=lambda row: (row["category_rank"], row["name"]))
    return {
        "operators": [{"id": str(u.id), "name": u.name} for u in operators],
        "procedures": [{"id": str(p.id), "code": p.code, "name": p.name} for p in procedures],
        "rooms": [{"id": str(r.id), "code": r.code, "name": r.name} for r in rooms],
        "stations": [{"id": str(s.id), "name": s.name, "room_id": str(s.room_id) if s.room_id else ""} for s in stations],
        "kits": [{"id": str(k.id), "name": k.name} for k in kits],
        "patients": [{"id": str(p.id), "name": p.display_name} for p in patients],
        "physicians": [{"id": str(p.id), "name": p.name} for p in physicians],
        "surgical_roles": [{"code": r.code, "name": r.name} for r in surgical_roles],
        "phases": [{"code": p.code, "name": p.name} for p in phases],
        "privacy_notices": [{
            "id": str(n.id),
            "version": n.version,
            "document_uri": n.document_uri,
            "effective_at": n.effective_at.isoformat() if n.effective_at else "",
        } for n in notices],
        "families": family_rows,
    }


def kit_expected_lines(kit_id: UUID) -> list[dict]:
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
    stock = stock_for_families(family_ids)
    lines = []
    for item, family in rows:
        s = stock.get(str(item.family_id), {"stock_total": 0, "stock_available": 0})
        requested = item.quantity
        category = categories.get(family.category_id)
        code = category.code if category else ""
        rank = purpose_order.index(code) if code in purpose_order else len(purpose_order)
        lines.append({
            "family_id": str(item.family_id),
            "family_name": family.name,
            "family_code": family.code,
            "expected_quantity": item.quantity,
            "stock_available": s["stock_available"],
            "stock_total": s["stock_total"],
            "semaphore": semaphore_for_qty(requested, s["stock_available"]),
            "category_code": code,
            "category_label": category.name if category else "Other",
            "category_rank": rank,
        })
    lines.sort(key=lambda row: (row["category_rank"], row["family_name"]))
    return lines


def stock_recheck_lines(lines: list[dict]) -> list[dict]:
    """Recalculate semaphore for arbitrary family/qty pairs (schedule qty edits)."""
    family_ids = [UUID(str(line["family_id"])) for line in lines if line.get("family_id")]
    stock = stock_for_families(family_ids)
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
    ip: str | None = None,
) -> dict:
    """RF-SP-06 resolve open discrepancy (optionally mark family piece lost or recovered)."""
    if mark_lost and mark_recovered:
        raise RfSessionError("Choose Lost or Recovered, not both.", 400)
    if not (notes or "").strip():
        raise RfSessionError("Resolution notes are required.", 400)

    disc = db.session.get(Discrepancy, discrepancy_id)
    if disc is None:
        raise RfSessionError("Discrepancy not found.", 404)
    if disc.resolved:
        raise RfSessionError("Discrepancy already resolved.", 409)
    work = db.session.get(WorkSession, disc.session_id)
    if work is None:
        raise RfSessionError("Session not found.", 404)

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
        raise RfSessionError("Could not resolve discrepancy; please retry.", 503) from exc
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
) -> dict:
    """RF-SP-05 administrative close when awaiting_spd_review and no open discrepancies."""
    if not material_recovered:
        raise RfSessionError("Confirm material recovered / reprocessing started.", 400)
    work = db.session.get(WorkSession, session_id)
    if work is None:
        raise RfSessionError("Session not found.", 404)
    status = db.session.get(CatSessionStatus, work.status_id)
    if status is None or status.code != "awaiting_spd_review":
        raise RfSessionError("Session is not awaiting SPD review.", 409)
    open_count = db.session.scalar(
        select(func.count()).select_from(Discrepancy).where(
            Discrepancy.session_id == session_id,
            Discrepancy.resolved.is_(False),
        )
    ) or 0
    if open_count:
        raise RfSessionError("Resolve open discrepancies before closing.", 409)

    now = utc_now()
    work.status_id = _status_id("closed")
    work.closed_by_user_id = supervisor_user_id
    work.updated_at = now
    if work.ended_at is None:
        work.ended_at = now

    sterilizing = _cycle_status_id("sterilization")
    if work.operation_id:
        reservations = db.session.execute(
            select(InstrumentReservation).where(
                InstrumentReservation.operation_id == work.operation_id,
                InstrumentReservation.active.is_(True),
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
                    notes="SPD confirm close — reprocessing started",
                ))

    db.session.add(CountEvent(
        event_type_id=_event_type_id("session_close"),
        session_id=session_id,
        user_id=supervisor_user_id,
        occurred_at=now,
        payload={"closed_by": "spd_supervisor", "material_recovered": True},
    ))
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
        raise RfSessionError("Could not close session; please retry.", 503) from exc
    return {"session_id": str(session_id), "status_code": "closed"}
