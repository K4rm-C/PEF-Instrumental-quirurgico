"""PostgreSQL access of the Supervisor operations (PEF-DS09 §1.3.4 complement v0.2.0).

Reads: the session snapshot (`expand=detail`) — the same data, order and shape the portal's local
loader produces (apps/BackendWebFlask/services/session_snapshot.py), so the RF screens render the
same whichever source they use. Writes: scheduling (operation + patient + surgical team + session
+ expected inventory), full edit of a scheduled session, privacy agreement (DS07) and the reads the
SPD rules need (count reference, contingency).

Every function receives an open Connection; tenant scoping (operation.institution_id) is part of
the WHERE clause. Plain SQL on purpose: it mirrors data/migrations/001_init.sql one to one.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import bindparam, text
from sqlalchemy.engine import Connection

_SUMMARY_SELECT = """
    SELECT ws.id, ws.capture_mode, ws.user_id, ws.started_at, ws.ended_at, ws.updated_at, ws.closed_by_user_id,
           ws.current_phase_id, ws.kit_id, ws.station_id,
           st.code AS status_code, st.name AS status_name,
           u.name AS operator_name,
           op.id AS operation_id, op.scheduled_at, op.institution_id,
           pt.id AS procedure_id, pt.code AS procedure_code, pt.name AS procedure_name,
           r.id AS room_id, r.code AS room_code, r.name AS room_name,
           k.name AS kit_name, cs.name AS station_name,
           (SELECT count(*) FROM discrepancy d WHERE d.session_id = ws.id AND d.resolved = FALSE) AS open_discrepancies,
           EXISTS (SELECT 1 FROM session_processing_agreement a WHERE a.session_id = ws.id) AS has_agreement
    FROM work_session ws
    JOIN operation op ON op.id = ws.operation_id
    JOIN cat_session_status st ON st.id = ws.status_id
    LEFT JOIN "user" u ON u.id = ws.user_id
    LEFT JOIN cat_procedure_type pt ON pt.id = op.procedure_type_id
    LEFT JOIN operating_room r ON r.id = op.room_id
    LEFT JOIN kit k ON k.id = ws.kit_id
    LEFT JOIN capture_station cs ON cs.id = ws.station_id
    WHERE op.institution_id = :institution_id
"""


def list_session_summaries(connection: Connection, institution_id: uuid.UUID, *,
                           operator_user_id: uuid.UUID | None = None,
                           status_codes: list[str] | None = None) -> list[dict[str, Any]]:
    sql = _SUMMARY_SELECT
    params: dict[str, Any] = {"institution_id": institution_id}
    if operator_user_id is not None:
        sql += " AND ws.user_id = :operator_user_id"
        params["operator_user_id"] = operator_user_id
    if status_codes:
        sql += " AND st.code IN :status_codes"
        params["status_codes"] = list(status_codes)
    sql += " ORDER BY ws.updated_at DESC, ws.id"
    statement = text(sql)
    if status_codes:
        statement = statement.bindparams(bindparam("status_codes", expanding=True))
    return [dict(row) for row in connection.execute(statement, params).mappings().all()]


def get_session_summary(connection: Connection, session_id: uuid.UUID, institution_id: uuid.UUID,
                        operator_user_id: uuid.UUID | None = None) -> dict[str, Any] | None:
    sql = _SUMMARY_SELECT + " AND ws.id = :session_id"
    params: dict[str, Any] = {"institution_id": institution_id, "session_id": session_id}
    if operator_user_id is not None:
        sql += " AND ws.user_id = :operator_user_id"
        params["operator_user_id"] = operator_user_id
    row = connection.execute(text(sql), params).mappings().first()
    return dict(row) if row else None


def get_phase(connection: Connection, phase_id: uuid.UUID | None) -> dict[str, Any] | None:
    if phase_id is None:
        return None
    row = connection.execute(text("SELECT id, code, name FROM cat_operation_phase WHERE id = :id"),
                             {"id": phase_id}).mappings().first()
    return dict(row) if row else None


def expected_inventory_detail(connection: Connection, session_id: uuid.UUID) -> list[dict[str, Any]]:
    rows = connection.execute(text("""
        SELECT ei.family_id, f.code AS family_code, f.name AS family_name, ei.expected_quantity, ei.source,
               c.id AS category_id, c.code AS category_code, c.name AS category_name
        FROM expected_inventory ei
        JOIN instrument_family f ON f.id = ei.family_id
        LEFT JOIN cat_instrument_category c ON c.id = f.category_id
        WHERE ei.session_id = :session_id
        ORDER BY f.name, f.id
    """), {"session_id": session_id}).mappings().all()
    return [dict(row) for row in rows]


def count_events_detail(connection: Connection, session_id: uuid.UUID) -> list[dict[str, Any]]:
    rows = connection.execute(text("""
        SELECT ce.id, ce.occurred_at, et.code AS event_code, et.name AS event_name, ce.user_id, ce.family_id,
               ce.expected_quantity, ce.detected_quantity, ce.payload
        FROM count_event ce
        JOIN cat_event_type et ON et.id = ce.event_type_id
        WHERE ce.session_id = :session_id
        ORDER BY ce.occurred_at,
                 CASE et.code WHEN 'session_open' THEN 0 WHEN 'manual_close' THEN 2 WHEN 'session_close' THEN 3 ELSE 1 END,
                 ce.id
    """), {"session_id": session_id}).mappings().all()
    return [dict(row) for row in rows]


_DISCREPANCY_SELECT = """
    SELECT d.id, d.session_id, d.description, d.resolved, d.resolved_at, d.expected_quantity, d.detected_quantity,
           f.id AS family_id, f.code AS family_code, f.name AS family_name,
           r.code AS reason_code, r.name AS reason_name, et.code AS origin_event_code
    FROM discrepancy d
    LEFT JOIN instrument_family f ON f.id = d.family_id
    LEFT JOIN cat_discrepancy_reason r ON r.id = d.reason_id
    LEFT JOIN count_event ce ON ce.id = d.origin_event_id
    LEFT JOIN cat_event_type et ON et.id = ce.event_type_id
"""


def session_discrepancies(connection: Connection, session_id: uuid.UUID) -> list[dict[str, Any]]:
    rows = connection.execute(text(_DISCREPANCY_SELECT + """
        WHERE d.session_id = :session_id
        ORDER BY d.resolved, f.name, d.id
    """), {"session_id": session_id}).mappings().all()
    return [dict(row) for row in rows]


def tenant_discrepancies(connection: Connection, institution_id: uuid.UUID, resolved: bool | None) -> list[dict[str, Any]]:
    sql = _DISCREPANCY_SELECT + """
        JOIN work_session ws ON ws.id = d.session_id
        JOIN operation op ON op.id = ws.operation_id
        WHERE op.institution_id = :institution_id
    """
    params: dict[str, Any] = {"institution_id": institution_id}
    if resolved is not None:
        sql += " AND d.resolved = :resolved"
        params["resolved"] = resolved
    sql += " ORDER BY d.updated_at DESC, d.id"
    return [dict(row) for row in connection.execute(text(sql), params).mappings().all()]


def privacy_agreement(connection: Connection, session_id: uuid.UUID) -> dict[str, Any] | None:
    row = connection.execute(text("""
        SELECT a.agreed_at, a.purpose_quality_ops, a.purpose_model_improvement, a.agreed_by_user_id,
               p.version, p.document_uri, u.name AS agreed_by_name
        FROM session_processing_agreement a
        LEFT JOIN privacy_notice_version p ON p.id = a.privacy_notice_version_id
        LEFT JOIN "user" u ON u.id = a.agreed_by_user_id
        WHERE a.session_id = :session_id
    """), {"session_id": session_id}).mappings().first()
    return dict(row) if row else None


def operation_patient(connection: Connection, operation_id: uuid.UUID) -> dict[str, Any] | None:
    row = connection.execute(text("""
        SELECT op.patient_id, p.display_name
        FROM operation_patient op LEFT JOIN patient p ON p.id = op.patient_id
        WHERE op.operation_id = :operation_id
        ORDER BY op.id LIMIT 1
    """), {"operation_id": operation_id}).mappings().first()
    return dict(row) if row else None


def surgical_team(connection: Connection, operation_id: uuid.UUID) -> list[dict[str, Any]]:
    rows = connection.execute(text("""
        SELECT ph.id AS physician_id, ph.name, sr.code AS role_code, sr.name AS role_name
        FROM operation_physician op
        JOIN physician ph ON ph.id = op.physician_id
        JOIN cat_surgical_role sr ON sr.id = op.surgical_role_id
        WHERE op.operation_id = :operation_id
        ORDER BY sr.name, ph.name, ph.id
    """), {"operation_id": operation_id}).mappings().all()
    return [dict(row) for row in rows]


def user_name(connection: Connection, user_id: uuid.UUID | None) -> str | None:
    if user_id is None:
        return None
    return connection.execute(text('SELECT name FROM "user" WHERE id = :id'), {"id": user_id}).scalar()


# --- SPD rules: count reference and contingency (same definitions as rf_session) -----------------


def events_of_type(connection: Connection, session_id: uuid.UUID, code: str, *,
                   board_only: bool = False) -> list[dict[str, Any]]:
    sql = """
        SELECT ce.id, ce.occurred_at, ce.user_id, ce.family_id, ce.expected_quantity, ce.detected_quantity, ce.payload
        FROM count_event ce JOIN cat_event_type et ON et.id = ce.event_type_id
        WHERE ce.session_id = :session_id AND et.code = :code
    """
    if board_only:
        sql += " AND ce.family_id IS NULL"
    sql += " ORDER BY ce.occurred_at, ce.id"
    return [dict(row) for row in connection.execute(text(sql), {"session_id": session_id, "code": code}).mappings()]


def count_reference(connection: Connection, session_id: uuid.UUID) -> dict[str, Any] | None:
    """Initial YOLO count + instruments_added confirmed after it (rf_session.count_reference)."""
    initial = None
    for event in events_of_type(connection, session_id, "auto_count", board_only=True):
        payload = event["payload"] if isinstance(event["payload"], dict) else {}
        if payload.get("trigger") == "initial":
            initial = event
            break
    if initial is None:
        return None
    additions = [event for event in events_of_type(connection, session_id, "instruments_added")
                 if event["occurred_at"] > initial["occurred_at"]]
    initial_total = int(initial["detected_quantity"] or 0)
    added = sum(int(event["detected_quantity"] or 0) for event in additions)
    return {"initial_total": initial_total, "added_total": added, "total": initial_total + added}


def has_contingency(connection: Connection, session_id: uuid.UUID) -> bool:
    """The Operator's last report was a contingency count (manual report instead of YOLO)."""
    closes = events_of_type(connection, session_id, "manual_close")
    if not closes:
        return False
    payload = closes[-1]["payload"] if isinstance(closes[-1]["payload"], dict) else {}
    return bool(payload.get("contingency"))


# --- Scheduling references (tenant checks, same rules as rf_session) ------------------------------


def get_user(connection: Connection, user_id: uuid.UUID) -> dict[str, Any] | None:
    row = connection.execute(text('SELECT id, active, institution_id FROM "user" WHERE id = :id'),
                             {"id": user_id}).mappings().first()
    return dict(row) if row else None


def has_effective_role(connection: Connection, user_id: uuid.UUID, institution_id: uuid.UUID, code: str) -> bool:
    return connection.execute(text("""
        SELECT 1 FROM user_role ur JOIN role r ON r.id = ur.role_id
        WHERE ur.user_id = :user_id AND r.code = :code AND r.institution_id = :institution_id
    """), {"user_id": user_id, "code": code, "institution_id": institution_id}).first() is not None


def get_room(connection: Connection, room_id: uuid.UUID) -> dict[str, Any] | None:
    row = connection.execute(text("SELECT id, institution_id FROM operating_room WHERE id = :id"),
                             {"id": room_id}).mappings().first()
    return dict(row) if row else None


def get_station(connection: Connection, station_id: uuid.UUID) -> dict[str, Any] | None:
    row = connection.execute(text("SELECT id, room_id FROM capture_station WHERE id = :id"),
                             {"id": station_id}).mappings().first()
    return dict(row) if row else None


def get_kit(connection: Connection, kit_id: uuid.UUID) -> dict[str, Any] | None:
    row = connection.execute(text("SELECT id, institution_id FROM kit WHERE id = :id"), {"id": kit_id}).mappings().first()
    return dict(row) if row else None


def get_patient(connection: Connection, patient_id: uuid.UUID) -> dict[str, Any] | None:
    row = connection.execute(text("SELECT id, institution_id FROM patient WHERE id = :id"),
                             {"id": patient_id}).mappings().first()
    return dict(row) if row else None


def get_physician(connection: Connection, physician_id: uuid.UUID) -> dict[str, Any] | None:
    row = connection.execute(text("SELECT id, active, institution_id FROM physician WHERE id = :id"),
                             {"id": physician_id}).mappings().first()
    return dict(row) if row else None


def surgical_role_id(connection: Connection, code: str) -> uuid.UUID | None:
    return connection.execute(text("SELECT id FROM cat_surgical_role WHERE code = :code"), {"code": code}).scalar()


def procedure_type_exists(connection: Connection, procedure_type_id: uuid.UUID) -> bool:
    return connection.execute(text("SELECT 1 FROM cat_procedure_type WHERE id = :id"),
                              {"id": procedure_type_id}).first() is not None


def phase_id_by_code(connection: Connection, code: str) -> uuid.UUID | None:
    return connection.execute(text("SELECT id FROM cat_operation_phase WHERE code = :code"), {"code": code}).scalar()


def operation_status_id(connection: Connection, code: str) -> uuid.UUID:
    value = connection.execute(text("SELECT id FROM cat_operation_status WHERE code = :code"), {"code": code}).scalar()
    if value is None:
        raise LookupError(f"cat_operation_status {code} missing")
    return value


def insert_operation(connection: Connection, values: dict[str, Any]) -> None:
    connection.execute(text("""
        INSERT INTO operation (id, scheduled_at, status_id, procedure_type_id, room_id, institution_id)
        VALUES (:id, :scheduled_at, :status_id, :procedure_type_id, :room_id, :institution_id)
    """), values)


def lock_operation_row(connection: Connection, operation_id: uuid.UUID) -> None:
    connection.execute(text("SELECT id FROM operation WHERE id = :id FOR UPDATE"), {"id": operation_id})


def update_operation(connection: Connection, operation_id: uuid.UUID, *, scheduled_at: datetime,
                     procedure_type_id: uuid.UUID, room_id: uuid.UUID) -> None:
    connection.execute(text("""
        UPDATE operation SET scheduled_at = :scheduled_at, procedure_type_id = :procedure_type_id, room_id = :room_id
        WHERE id = :id
    """), {"id": operation_id, "scheduled_at": scheduled_at, "procedure_type_id": procedure_type_id, "room_id": room_id})


def replace_operation_people(connection: Connection, operation_id: uuid.UUID, patient_id: uuid.UUID | None,
                             team: list[tuple[uuid.UUID, uuid.UUID]]) -> None:
    connection.execute(text("DELETE FROM operation_patient WHERE operation_id = :id"), {"id": operation_id})
    connection.execute(text("DELETE FROM operation_physician WHERE operation_id = :id"), {"id": operation_id})
    if patient_id is not None:
        connection.execute(text("INSERT INTO operation_patient (id, operation_id, patient_id) VALUES (:id, :op, :p)"),
                           {"id": uuid.uuid4(), "op": operation_id, "p": patient_id})
    for physician_id, role_id in team:
        connection.execute(text("""
            INSERT INTO operation_physician (id, operation_id, physician_id, surgical_role_id) VALUES (:id, :op, :ph, :r)
        """), {"id": uuid.uuid4(), "op": operation_id, "ph": physician_id, "r": role_id})


def update_session_assignment(connection: Connection, session_id: uuid.UUID, *, user_id: uuid.UUID,
                              station_id: uuid.UUID, kit_id: uuid.UUID, now: datetime) -> None:
    connection.execute(text("""
        UPDATE work_session SET user_id = :user_id, station_id = :station_id, kit_id = :kit_id, updated_at = :now
        WHERE id = :id
    """), {"id": session_id, "user_id": user_id, "station_id": station_id, "kit_id": kit_id, "now": now})


# --- Privacy agreement (DS07, RF-SP-02 Via A) -------------------------------------------------------


def privacy_notice(connection: Connection, notice_id: uuid.UUID) -> dict[str, Any] | None:
    row = connection.execute(text("SELECT id, version, active FROM privacy_notice_version WHERE id = :id"),
                             {"id": notice_id}).mappings().first()
    return dict(row) if row else None


def upsert_agreement(connection: Connection, session_id: uuid.UUID, notice_id: uuid.UUID,
                     model_improvement: bool, user_id: uuid.UUID, now: datetime) -> None:
    updated = connection.execute(text("""
        UPDATE session_processing_agreement
        SET privacy_notice_version_id = :notice, purpose_quality_ops = TRUE, purpose_model_improvement = :mi,
            agreed_at = :now, agreed_by_user_id = :user_id
        WHERE session_id = :session_id
    """), {"notice": notice_id, "mi": model_improvement, "now": now, "user_id": user_id, "session_id": session_id})
    if updated.rowcount == 0:
        connection.execute(text("""
            INSERT INTO session_processing_agreement (id, session_id, privacy_notice_version_id, purpose_quality_ops,
                                                      purpose_model_improvement, agreed_at, agreed_by_user_id)
            VALUES (:id, :session_id, :notice, TRUE, :mi, :now, :user_id)
        """), {"id": uuid.uuid4(), "session_id": session_id, "notice": notice_id, "mi": model_improvement,
               "now": now, "user_id": user_id})


# --- Discrepancy piece outcomes chosen by family (RF rule when no instrument_id is given) -----------


def first_active_piece_of_family(connection: Connection, family_id: uuid.UUID, institution_id: uuid.UUID,
                                 operation_id: uuid.UUID | None) -> uuid.UUID | None:
    """An active piece of the family in service (available / reserved / in_use): reserved for this
    operation first, then by id (deterministic)."""
    return connection.execute(text("""
        SELECT i.id FROM instrument i JOIN cat_instrument_cycle_status s ON s.id = i.cycle_status_id
        WHERE i.family_id = :family_id AND i.institution_id = :institution_id AND i.active = TRUE
          AND s.code IN ('available', 'reserved', 'in_use')
        ORDER BY (EXISTS (SELECT 1 FROM instrument_reservation r
                          WHERE r.instrument_id = i.id AND r.active AND r.operation_id = :operation_id)) DESC, i.id
        LIMIT 1
    """), {"family_id": family_id, "institution_id": institution_id, "operation_id": operation_id}).scalar()


def first_lost_piece_of_family(connection: Connection, family_id: uuid.UUID, institution_id: uuid.UUID) -> uuid.UUID | None:
    return connection.execute(text("""
        SELECT i.id FROM instrument i JOIN cat_instrument_cycle_status s ON s.id = i.cycle_status_id
        WHERE i.family_id = :family_id AND i.institution_id = :institution_id AND s.code = 'lost'
        ORDER BY i.id LIMIT 1
    """), {"family_id": family_id, "institution_id": institution_id}).scalar()
