"""Event → notification rules (the only place that decides who is told what).

Every rule reads the CURRENT rows of PostgreSQL (work_session, operation, access_audit, users and
roles); nothing is copied from the request of the producer. Recipients must be active users of the
session's institution; the actor of an event is not notified of their own action (except
correction_required: the system's answer to the Operator's own report).

| Outbox event (source)                         | Notification type     | Recipients                                   |
| access_audit session.scheduled                | session_scheduled     | assigned operator (work_session.user_id)     |
| count_event manual_close, next_status =       | discrepancy_pending   | supervisor who scheduled the session; if      |
|   correction_required                         |                       | unknown, every active SPD supervisor of the   |
|                                               |                       | institution                                   |
|                                               | correction_required   | assigned operator                             |
| access_audit discrepancy.resolve              | discrepancy_resolved  | assigned operator (one per session while it   |
|                                               |                       | is unread: resolving N discrepancies in a     |
|                                               |                       | row is a single notice)                       |
| count_event session_close                     | session_closed        | assigned operator + scheduling supervisor     |

MS-06 additions:
| count_event manual_close, next_status = awaiting_spd_review -> review_pending (scheduling supervisor, else every
|   active SPD supervisor of the institution)
| count_event session_close with safe_close_confirmed = false  -> session_unresolved (operator + scheduling
|   supervisor), instead of session_closed
| station_event heartbeat_lost / camera_error                  -> station_alert (active SPD supervisors and IT
|   administrators of the station's institution; one unread alert per station and kind at a time)

MS-11 additions:
| access_audit session.final_count_escalated -> final_count_escalated (scheduling supervisor, else every active SPD
|   supervisor of the institution): the Operator reported a possible physical difference after a rejected final count
| service_event down / recovered              -> service_alert (every active IT administrator, in their own institution;
|   one unread alert per component and kind): technical availability only, never a statement about counts

Content is minimal and non-clinical: the session label (WS-XXXXXXXX) and, where it exists, the
scheduled time or the number of open discrepancies. Never patient, physician or free text.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import timezone
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Connection

logger = logging.getLogger("notifications.dispatcher")

SUPERVISOR = "spd_supervisor"
ADMINISTRATOR = "it_admin"


def session_label(session_id) -> str:
    return "WS-" + str(session_id).replace("-", "")[:8].upper()


def _iso(value):
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z") if value else None


def _session(connection: Connection, session_id) -> dict[str, Any] | None:
    row = connection.execute(text("""
        SELECT w.id, w.user_id, o.institution_id, o.scheduled_at
        FROM work_session w JOIN operation o ON o.id = w.operation_id WHERE w.id = :id
    """), {"id": session_id}).mappings().first()
    return dict(row) if row else None


def _scheduler(connection: Connection, session_id) -> uuid.UUID | None:
    return connection.execute(text("""
        SELECT actor_user_id FROM access_audit
        WHERE resource_type = 'work_session' AND resource_id = :id AND action = 'session.scheduled'
        ORDER BY occurred_at, id LIMIT 1
    """), {"id": session_id}).scalar()


def _active_members(connection: Connection, user_ids, institution_id) -> list[uuid.UUID]:
    ids = [uid for uid in dict.fromkeys(user_ids) if uid]
    if not ids:
        return []
    rows = connection.execute(text(
        'SELECT id FROM "user" WHERE id = ANY(:ids) AND active AND institution_id = :inst'),
        {"ids": ids, "inst": institution_id}).scalars().all()
    active = set(rows)
    return [uid for uid in ids if uid in active]


def _supervisors(connection: Connection, institution_id, code: str = SUPERVISOR) -> list[uuid.UUID]:
    return list(connection.execute(text("""
        SELECT DISTINCT u.id FROM "user" u
        JOIN user_role ur ON ur.user_id = u.id
        JOIN role r ON r.id = ur.role_id AND r.institution_id = u.institution_id
        WHERE u.institution_id = :inst AND u.active AND r.code = :code
        ORDER BY u.id
    """), {"inst": institution_id, "code": code}).scalars())


def _station_plan(connection: Connection, event: dict[str, Any]) -> list[dict[str, Any]]:
    """MS-05/06: a station alert, for the supervisors and administrators of the station's institution."""
    row = connection.execute(text("""
        SELECT e.kind, e.institution_id, e.station_id, s.name AS station_name, c.name AS camera_name, e.detail
        FROM station_event e JOIN capture_station s ON s.id = e.station_id
        LEFT JOIN capture_camera c ON c.id = e.camera_id WHERE e.id = :id
    """), {"id": event["source_id"]}).mappings().first()
    if row is None or row["kind"] not in ("heartbeat_lost", "camera_error"):
        return []
    detail = row["detail"] if isinstance(row["detail"], dict) else {}
    recipients = _supervisors(connection, row["institution_id"]) + _supervisors(connection, row["institution_id"],
                                                                                ADMINISTRATOR)
    out = []
    for user_id in dict.fromkeys(recipients):
        # One unread alert per station and kind: a flapping station does not flood the bell.
        unread = connection.execute(text("""
            SELECT 1 FROM notification WHERE recipient_user_id = :u AND type = 'station_alert' AND status = 'unread'
              AND data ->> 'station_id' = :station AND data ->> 'kind' = :kind LIMIT 1
        """), {"u": user_id, "station": str(row["station_id"]), "kind": row["kind"]}).first()
        if unread:
            continue
        out.append({"type": "station_alert", "recipient_user_id": user_id, "institution_id": row["institution_id"],
                    "session_id": None, "data": {"station_id": str(row["station_id"]), "station_name": row["station_name"],
                                                 "kind": row["kind"], "camera_name": row["camera_name"],
                                                 "error": detail.get("error")}})
    return out


def _service_plan(connection: Connection, event: dict[str, Any]) -> list[dict[str, Any]]:
    """MS-11: a component of the platform went down / recovered (MonitoringService). Infrastructure is shared, so
    every active IT administrator is told, each inside their own institution."""
    row = connection.execute(text("""
        SELECT component, kind, status, error_type, occurred_at FROM service_event WHERE id = :id
    """), {"id": event["source_id"]}).mappings().first()
    if row is None:
        return []
    admins = connection.execute(text("""
        SELECT DISTINCT u.id, u.institution_id FROM "user" u
        JOIN user_role ur ON ur.user_id = u.id
        JOIN role r ON r.id = ur.role_id AND r.institution_id = u.institution_id
        WHERE u.active AND r.code = :code ORDER BY u.id
    """), {"code": ADMINISTRATOR}).mappings().all()
    out = []
    for admin in admins:
        unread = connection.execute(text("""
            SELECT 1 FROM notification WHERE recipient_user_id = :u AND type = 'service_alert' AND status = 'unread'
              AND data ->> 'component' = :component AND data ->> 'kind' = :kind LIMIT 1
        """), {"u": admin["id"], "component": row["component"], "kind": row["kind"]}).first()
        if unread:
            continue
        out.append({"type": "service_alert", "recipient_user_id": admin["id"], "institution_id": admin["institution_id"],
                    "session_id": None, "data": {"component": row["component"], "kind": row["kind"],
                                                 "status": row["status"], "error": row["error_type"],
                                                 "occurred_at": _iso(row["occurred_at"])}})
    return out


def _has_unread(connection: Connection, user_id, session_id, kind: str) -> bool:
    return connection.execute(text("""
        SELECT 1 FROM notification
        WHERE recipient_user_id = :u AND session_id = :s AND type = :t AND status = 'unread' LIMIT 1
    """), {"u": user_id, "s": session_id, "t": kind}).first() is not None


def _source(connection: Connection, event: dict[str, Any]) -> dict[str, Any] | None:
    if event["source_table"] == "access_audit":
        row = connection.execute(text("SELECT actor_user_id AS actor, '{}'::jsonb AS payload FROM access_audit WHERE id = :id"),
                                 {"id": event["source_id"]}).mappings().first()
    else:
        row = connection.execute(text("SELECT user_id AS actor, payload FROM count_event WHERE id = :id"),
                                 {"id": event["source_id"]}).mappings().first()
    return dict(row) if row else None


def plan(connection: Connection, event: dict[str, Any]) -> list[dict[str, Any]]:
    """Notifications an outbox event produces (possibly none). Pure read; no writes."""
    if event["source_table"] == "station_event":
        return _station_plan(connection, event)
    if event["source_table"] == "service_event":
        return _service_plan(connection, event)
    source = _source(connection, event)
    session = _session(connection, event["session_id"]) if event["session_id"] else None
    if source is None or session is None:
        return []   # source row or session gone (e.g. test data removed): nothing to tell anybody
    institution = session["institution_id"]
    label = session_label(session["id"])
    actor = source["actor"]
    payload = source["payload"] if isinstance(source["payload"], dict) else {}
    out: list[dict[str, Any]] = []

    def add(kind: str, recipients, data: dict[str, Any], *, skip_actor: bool = True, collapse: bool = False) -> None:
        for user_id in _active_members(connection, recipients, institution):
            if skip_actor and user_id == actor:
                continue
            if collapse and _has_unread(connection, user_id, session["id"], kind):
                continue
            out.append({"type": kind, "recipient_user_id": user_id, "institution_id": institution,
                        "session_id": session["id"], "data": {"session_label": label, **data}})

    code = event["event_code"]
    if code == "session.scheduled":
        add("session_scheduled", [session["user_id"]], {"scheduled_at": _iso(session["scheduled_at"])})
    elif code == "manual_close" and payload.get("next_status") == "correction_required":
        open_count = payload.get("open_discrepancies")
        data = {"open_discrepancies": open_count} if isinstance(open_count, int) else {}
        scheduler = _scheduler(connection, session["id"])
        supervisors = _active_members(connection, [scheduler], institution) or _supervisors(connection, institution)
        add("discrepancy_pending", supervisors, data)
        # The Operator wrote the report, but this is the system telling them the session now needs
        # SPD reconciliation: they are told even though they are the event's actor.
        add("correction_required", [session["user_id"]], data, skip_actor=False)
    elif code == "manual_close" and payload.get("next_status") == "awaiting_spd_review":
        # MS-06: the Operator finished without open discrepancies; the Supervisor has to review / close.
        scheduler = _scheduler(connection, session["id"])
        add("review_pending", _active_members(connection, [scheduler], institution) or _supervisors(connection, institution),
            {})
    elif code == "session.final_count_escalated":
        # MS-11: the Operator could not reconcile the final count and reported a possible physical difference.
        scheduler = _scheduler(connection, session["id"])
        add("final_count_escalated", _active_members(connection, [scheduler], institution) or _supervisors(connection, institution),
            {})
    elif code == "discrepancy.resolve":
        add("discrepancy_resolved", [session["user_id"]], {}, collapse=True)
    elif code == "session_close" and payload.get("safe_close_confirmed") is False:
        # MS-06: closed with an unreconciled physical shortage — not an approval.
        add("session_unresolved", [session["user_id"], _scheduler(connection, session["id"])], {})
    elif code == "session_close":
        add("session_closed", [session["user_id"], _scheduler(connection, session["id"])], {})
    return out


def store(connection: Connection, event: dict[str, Any], notifications: list[dict[str, Any]],
          email_types: tuple[str, ...] = ()) -> int:
    """Insert once per (event, recipient, type): replaying an event never duplicates a notification.

    MS-06: a new notification of a type in `email_types` (only when the e-mail channel is enabled) also gets
    a pending e-mail delivery, in the same transaction; sending happens later and never touches it.
    """
    created = 0
    for item in notifications:
        notification_id = connection.execute(text("""
            INSERT INTO notification (recipient_user_id, institution_id, type, session_id, data, source_table, source_id)
            VALUES (:recipient, :inst, :type, :session, CAST(:data AS jsonb), :source_table, :source_id)
            ON CONFLICT (source_table, source_id, recipient_user_id, type) DO NOTHING
            RETURNING id
        """), {"recipient": item["recipient_user_id"], "inst": item["institution_id"], "type": item["type"],
               "session": item["session_id"], "data": json.dumps(item["data"]),
               "source_table": event["source_table"], "source_id": event["source_id"]}).scalar()
        if notification_id is None:
            continue
        created += 1
        if item["type"] in email_types:
            connection.execute(text("""
                INSERT INTO notification_delivery (notification_id, channel) VALUES (:n, 'email')
                ON CONFLICT (notification_id, channel) DO NOTHING
            """), {"n": notification_id})
    return created
