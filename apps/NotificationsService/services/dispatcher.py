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


def _supervisors(connection: Connection, institution_id) -> list[uuid.UUID]:
    return list(connection.execute(text("""
        SELECT DISTINCT u.id FROM "user" u
        JOIN user_role ur ON ur.user_id = u.id
        JOIN role r ON r.id = ur.role_id AND r.institution_id = u.institution_id
        WHERE u.institution_id = :inst AND u.active AND r.code = :code
        ORDER BY u.id
    """), {"inst": institution_id, "code": SUPERVISOR}).scalars())


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
    elif code == "discrepancy.resolve":
        add("discrepancy_resolved", [session["user_id"]], {}, collapse=True)
    elif code == "session_close":
        add("session_closed", [session["user_id"], _scheduler(connection, session["id"])], {})
    return out


def store(connection: Connection, event: dict[str, Any], notifications: list[dict[str, Any]]) -> int:
    """Insert once per (event, recipient, type): replaying an event never duplicates a notification."""
    created = 0
    for item in notifications:
        result = connection.execute(text("""
            INSERT INTO notification (recipient_user_id, institution_id, type, session_id, data, source_table, source_id)
            VALUES (:recipient, :inst, :type, :session, CAST(:data AS jsonb), :source_table, :source_id)
            ON CONFLICT (source_table, source_id, recipient_user_id, type) DO NOTHING
        """), {"recipient": item["recipient_user_id"], "inst": item["institution_id"], "type": item["type"],
               "session": item["session_id"], "data": json.dumps(item["data"]),
               "source_table": event["source_table"], "source_id": event["source_id"]})
        created += result.rowcount
    return created
