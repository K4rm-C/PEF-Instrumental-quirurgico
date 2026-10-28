"""Session snapshot: the plain data behind the RF session screens (PEF-DS09 §1.3.4 `expand=detail`).

One representation, two sources:
- local (this module): read from PostgreSQL by the portal. Used by the Operator screens, which
  must never depend on SessionService, and by the Supervisor screens when SESSION_SERVICE_URL
  is not configured;
- remote: the same JSON returned by SessionService `GET /api/v1/sessions?expand=detail`,
  `GET /api/v1/sessions/{id}?expand=detail` and `GET /api/v1/sessions/discrepancies`
  (services/session_remote.py), used by the Supervisor screens.

controllers/view_data.py turns a snapshot into the RF rows (labels, grouping, localization), so
both sources render identically; tests/test_session_snapshot.py and SessionService's parity test
check the two sources give the same snapshot for the same session.

Dates are ISO-8601 UTC strings ("...Z"); count_event payloads are projected to the keys the
screens use (project_payload), never copied whole.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from sqlalchemy import case, func, literal_column, select

from extensions import db
from models.CaptureStation import CaptureStation
from models.CatDiscrepancyReason import CatDiscrepancyReason
from models.CatEventType import CatEventType
from models.CatInstrumentCategory import CatInstrumentCategory
from models.CatOperationPhase import CatOperationPhase
from models.CatProcedureType import CatProcedureType
from models.CatSessionStatus import CatSessionStatus
from models.CatSurgicalRole import CatSurgicalRole
from models.CountEvent import CountEvent
from models.Discrepancy import Discrepancy
from models.ExpectedInventory import ExpectedInventory
from models.InstrumentFamily import InstrumentFamily
from models.Kit import Kit
from models.OperatingRoom import OperatingRoom
from models.Operation import Operation
from models.OperationPatient import OperationPatient
from models.OperationPhysician import OperationPhysician
from models.Patient import Patient
from models.Physician import Physician
from models.PrivacyNoticeVersion import PrivacyNoticeVersion
from models.SessionProcessingAgreement import SessionProcessingAgreement
from models.User import User
from models.WorkSession import WorkSession

# count_event.payload keys the screens read (timeline, AI / human quantities, contingency).
PAYLOAD_SCALAR_KEYS = ("reason", "scope", "trigger", "reported_quantity", "frames_processed", "video_t", "pipeline",
                       "solver", "model_version", "verified_total")
PAYLOAD_OBJECT_KEYS = ("contingency", "reference", "known_final_mismatch")
# Events sharing occurred_at: the opening first, the facts next, the closing events last (same rule
# as SessionService GET /counts), then id.
EVENT_ORDER_RANK = case(
    (CatEventType.code == "session_open", 0),
    (CatEventType.code == "manual_close", 2),
    (CatEventType.code == "session_close", 3),
    else_=1,
)


def iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def project_payload(payload: Any) -> dict[str, Any]:
    """The part of a count_event payload the RF screens use (same rule in SessionService)."""
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


def _ref(row, *fields) -> dict[str, Any] | None:
    if row is None:
        return None
    return {"id": str(row.id), **{name: getattr(row, name) for name in fields}}


# --- Summary (lists) ---------------------------------------------------------------------------


def _summary(work: WorkSession, *, status, operation, operator_name, kit, station, procedure, room,
             open_discrepancies: int, has_agreement: bool) -> dict[str, Any]:
    return {
        "id": str(work.id),
        "status": {"code": status.code, "name": status.name} if status else None,
        "capture_mode": work.capture_mode,
        "operator": {"id": str(work.user_id), "name": operator_name} if work.user_id else None,
        "operation": {
            "id": str(operation.id),
            "scheduled_at": iso(operation.scheduled_at),
            "institution_id": str(operation.institution_id),
            "procedure_type": _ref(procedure, "code", "name"),
            "room": _ref(room, "code", "name"),
        } if operation else None,
        "kit": _ref(kit, "name"),
        "station": _ref(station, "name"),
        "started_at": iso(work.started_at),
        "ended_at": iso(work.ended_at),
        "updated_at": iso(work.updated_at),
        "closed_by_user_id": str(work.closed_by_user_id) if work.closed_by_user_id else None,
        "open_discrepancies": int(open_discrepancies or 0),
        "has_privacy_agreement": bool(has_agreement),
    }


def _lookups():
    return {
        "statuses": {item.id: item for item in db.session.execute(select(CatSessionStatus)).scalars()},
        "operations": {item.id: item for item in db.session.execute(select(Operation)).scalars()},
        "users": {item.id: item.name for item in db.session.execute(select(User)).scalars()},
        "kits": {item.id: item for item in db.session.execute(select(Kit)).scalars()},
        "stations": {item.id: item for item in db.session.execute(select(CaptureStation)).scalars()},
        "procedures": {item.id: item for item in db.session.execute(select(CatProcedureType)).scalars()},
        "rooms": {item.id: item for item in db.session.execute(select(OperatingRoom)).scalars()},
        "open": dict(db.session.execute(
            select(Discrepancy.session_id, func.count(Discrepancy.id))
            .where(Discrepancy.resolved.is_(False)).group_by(Discrepancy.session_id)
        ).all()),
        "agreements": {row[0] for row in db.session.execute(select(SessionProcessingAgreement.session_id)).all()},
    }


def _summary_from_lookups(work: WorkSession, maps) -> dict[str, Any]:
    operation = maps["operations"].get(work.operation_id)
    return _summary(
        work,
        status=maps["statuses"].get(work.status_id),
        operation=operation,
        operator_name=maps["users"].get(work.user_id),
        kit=maps["kits"].get(work.kit_id),
        station=maps["stations"].get(work.station_id),
        procedure=maps["procedures"].get(getattr(operation, "procedure_type_id", None)),
        room=maps["rooms"].get(getattr(operation, "room_id", None)),
        open_discrepancies=maps["open"].get(work.id, 0),
        has_agreement=work.id in maps["agreements"],
    )


def list_sessions(institution_id: UUID, operator_user_id: UUID | None = None) -> list[dict[str, Any]]:
    """Sessions of the tenant (optionally one operator's), most recently updated first."""
    query = (select(WorkSession).join(Operation, Operation.id == WorkSession.operation_id)
             .where(Operation.institution_id == institution_id))
    if operator_user_id is not None:
        query = query.where(WorkSession.user_id == operator_user_id)
    rows = db.session.execute(query.order_by(WorkSession.updated_at.desc(), WorkSession.id)).scalars().all()
    maps = _lookups()
    return [_summary_from_lookups(work, maps) for work in rows]


# --- Detail ------------------------------------------------------------------------------------


def session_detail(session_id: UUID, institution_id: UUID) -> dict[str, Any] | None:
    """Full snapshot of one session of the tenant, or None (unknown / other tenant / no operation)."""
    work = db.session.get(WorkSession, session_id)
    if work is None or work.operation_id is None:
        return None
    operation = db.session.get(Operation, work.operation_id)
    if operation is None or operation.institution_id != institution_id:
        return None
    maps = _lookups()
    snapshot = _summary_from_lookups(work, maps)
    phase = db.session.get(CatOperationPhase, work.current_phase_id) if work.current_phase_id else None
    snapshot["phase"] = _ref(phase, "code", "name")

    categories = {item.id: item for item in db.session.execute(select(CatInstrumentCategory)).scalars()}
    expected = db.session.execute(
        select(ExpectedInventory, InstrumentFamily)
        .join(InstrumentFamily, InstrumentFamily.id == ExpectedInventory.family_id)
        .where(ExpectedInventory.session_id == work.id)
        .order_by(InstrumentFamily.name, InstrumentFamily.id)
    ).all()
    snapshot["expected_inventory"] = [{
        "family_id": str(inv.family_id),
        "family_code": family.code,
        "family_name": family.name,
        "category": _ref(categories.get(family.category_id), "code", "name"),
        "expected_quantity": int(inv.expected_quantity),
        "source": inv.source,
    } for inv, family in expected]

    events = db.session.execute(
        select(CountEvent, CatEventType)
        .join(CatEventType, CatEventType.id == CountEvent.event_type_id)
        .where(CountEvent.session_id == work.id)
        .order_by(CountEvent.occurred_at, EVENT_ORDER_RANK, CountEvent.id)
    ).all()
    snapshot["events"] = [{
        "id": str(event.id),
        "occurred_at": iso(event.occurred_at),
        "event_code": event_type.code,
        "event_name": event_type.name,
        "user_id": str(event.user_id) if event.user_id else None,
        "family_id": str(event.family_id) if event.family_id else None,
        "expected_quantity": event.expected_quantity,
        "detected_quantity": event.detected_quantity,
        "payload": project_payload(event.payload),
    } for event, event_type in events]

    discrepancies = db.session.execute(
        select(Discrepancy, InstrumentFamily, CatDiscrepancyReason.code, CatEventType.code)
        .outerjoin(InstrumentFamily, InstrumentFamily.id == Discrepancy.family_id)
        .outerjoin(CatDiscrepancyReason, CatDiscrepancyReason.id == Discrepancy.reason_id)
        .outerjoin(CountEvent, CountEvent.id == Discrepancy.origin_event_id)
        .outerjoin(CatEventType, CatEventType.id == CountEvent.event_type_id)
        .where(Discrepancy.session_id == work.id)
        .order_by(Discrepancy.resolved, InstrumentFamily.name, Discrepancy.id)
    ).all()
    snapshot["discrepancies"] = [_discrepancy(disc, family, reason_code, origin_code)
                                 for disc, family, reason_code, origin_code in discrepancies]

    agreement = db.session.execute(
        select(SessionProcessingAgreement).where(SessionProcessingAgreement.session_id == work.id)
    ).scalar_one_or_none()
    if agreement:
        notice = db.session.get(PrivacyNoticeVersion, agreement.privacy_notice_version_id)
        agreed_by = agreement.agreed_by_user_id
        snapshot["privacy"] = {
            "version": notice.version if notice else None,
            "document_uri": notice.document_uri if notice else None,
            "agreed_at": iso(agreement.agreed_at),
            "purpose_quality_ops": bool(agreement.purpose_quality_ops),
            "purpose_model_improvement": bool(agreement.purpose_model_improvement),
            "agreed_by": {"id": str(agreed_by), "name": maps["users"].get(agreed_by)} if agreed_by else None,
        }
    else:
        snapshot["privacy"] = None

    op_patient = db.session.scalar(
        select(OperationPatient).where(OperationPatient.operation_id == operation.id)
        .order_by(OperationPatient.id).limit(1)
    )
    patient = db.session.get(Patient, op_patient.patient_id) if op_patient else None
    snapshot["patient"] = ({"id": str(op_patient.patient_id), "display_name": patient.display_name if patient else None}
                           if op_patient else None)
    team = db.session.execute(
        select(OperationPhysician, Physician, CatSurgicalRole)
        .join(Physician, Physician.id == OperationPhysician.physician_id)
        .join(CatSurgicalRole, CatSurgicalRole.id == OperationPhysician.surgical_role_id)
        .where(OperationPhysician.operation_id == operation.id)
        .order_by(CatSurgicalRole.name, Physician.name, Physician.id)
    ).all()
    snapshot["surgical_team"] = [{"physician_id": str(physician.id), "name": physician.name,
                                  "role_code": role.code, "role_name": role.name} for _, physician, role in team]
    snapshot["contingency"] = contingency_from_events(snapshot["events"], maps["users"])
    return snapshot


def _discrepancy(disc, family, reason_code, origin_code) -> dict[str, Any]:
    return {
        "id": str(disc.id),
        "session_id": str(disc.session_id),
        "description": disc.description,
        "resolved": bool(disc.resolved),
        "resolved_at": iso(disc.resolved_at),
        "expected_quantity": disc.expected_quantity,
        "detected_quantity": disc.detected_quantity,
        "family": _ref(family, "code", "name"),
        "reason_code": reason_code,
        "origin_event_code": origin_code,
    }


def contingency_from_events(events: list[dict[str, Any]], user_names) -> dict[str, Any] | None:
    """Contingency count of the session (last manual_close with a contingency), as in rf_session."""
    closes = [event for event in events if event["event_code"] == "manual_close"]
    if not closes:
        return None
    last = closes[-1]
    payload = last["payload"]
    if not payload.get("contingency"):
        return None
    user_id = last["user_id"]
    name = user_names.get(UUID(user_id)) if user_id and isinstance(user_names, dict) else None
    return {**payload["contingency"], "event_id": last["id"], "occurred_at": last["occurred_at"],
            "user_id": user_id, "user_name": name, "verified_total": payload.get("verified_total"),
            "reference": payload.get("reference"), "known_final_mismatch": payload.get("known_final_mismatch")}


# --- Discrepancies across the tenant (Supervisor queue) ----------------------------------------


def list_discrepancies(institution_id: UUID) -> list[dict[str, Any]]:
    tenant_sessions = (select(WorkSession.id).join(Operation, Operation.id == WorkSession.operation_id)
                       .where(Operation.institution_id == institution_id))
    rows = db.session.execute(
        select(Discrepancy, InstrumentFamily, CatDiscrepancyReason.code, CatEventType.code)
        .outerjoin(InstrumentFamily, InstrumentFamily.id == Discrepancy.family_id)
        .outerjoin(CatDiscrepancyReason, CatDiscrepancyReason.id == Discrepancy.reason_id)
        .outerjoin(CountEvent, CountEvent.id == Discrepancy.origin_event_id)
        .outerjoin(CatEventType, CatEventType.id == CountEvent.event_type_id)
        .where(Discrepancy.session_id.in_(tenant_sessions))
        # Most recently changed first (the ORM model does not map updated_at).
        .order_by(literal_column("discrepancy.updated_at").desc(), Discrepancy.id)
    ).all()
    reasons = {item.code: item.name for item in db.session.execute(select(CatDiscrepancyReason)).scalars()}
    result = []
    for disc, family, reason_code, origin_code in rows:
        item = _discrepancy(disc, family, reason_code, origin_code)
        item["reason_name"] = reasons.get(reason_code)
        result.append(item)
    return result
