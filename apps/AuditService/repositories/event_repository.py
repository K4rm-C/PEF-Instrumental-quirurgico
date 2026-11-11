"""Read model of the PEF audit / event trail over the EXISTING tables (no second log).

Two record types, one view (`events` CTE):
- access_audit (PEF-DS07): what users did — written transactionally by AuthService, CatalogService,
  SessionService, EvidenceService and the portal (Administration, Operator flow);
- count_event (PEF-DS04): the operational facts of a counting session (session_open, auto_count
  initial/final, close_blocked, manual_count, manual_close, instruments_added, session_close, …),
  written by the portal's Operator flow and SessionService.

Neither table records which process wrote a row, so `source_service` is the owner of the domain the
row belongs to (rules below), and `category` classifies it: security (identity, access, denials),
business (catalog, configuration, Supervisor session decisions) or operation (the counting flow and
evidence capture). Both are derived in SQL so they can be filtered there.

Tenant: access_audit.institution_id (the actor's institution, always set by every writer);
count_event → work_session → operation.institution_id.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import bindparam, text
from sqlalchemy.engine import Connection

OPERATOR_ACTIONS = ("session.start", "session.manual_close", "session.contingency_close")
AUTH_ACTIONS = ("LOGIN", "LOGIN_FAILED", "LOGOUT", "CHANGE_OWN_PASSWORD")
SUPERVISOR_RESOURCES = ("work_session", "instrument", "kit")

EVENTS_CTE = f"""
WITH events AS (
    SELECT a.id, 'access_audit'::text AS record_type, a.occurred_at, a.actor_type, a.actor_user_id AS user_id,
           a.action, a.resource_type, a.resource_id, a.institution_id, a.outcome, a.correlation_id,
           host(a.ip) AS ip,
           CASE WHEN a.resource_type = 'work_session' THEN a.resource_id END AS session_id,
           NULL::smallint AS expected_quantity, NULL::smallint AS detected_quantity, NULL::uuid AS family_id,
           NULL::jsonb AS payload,
           CASE
               WHEN a.outcome = 'denied' OR a.action LIKE 'DENIED\\_%' OR a.action IN {AUTH_ACTIONS + ("RESET_USER_PASSWORD", "read", "evidence.download_url")}
                    OR a.resource_type IN ('user', 'role') THEN 'security'
               WHEN a.action IN {OPERATOR_ACTIONS + ("evidence.upload",)} THEN 'operation'
               ELSE 'business'
           END AS category,
           CASE
               WHEN a.resource_type IN ('user', 'role') OR a.action IN {AUTH_ACTIONS} THEN 'auth'
               WHEN a.resource_type IN ('instrument_family', 'instrument', 'kit') THEN 'catalog'
               WHEN a.resource_type = 'media_asset' OR a.action LIKE 'evidence.%' THEN 'evidence'
               WHEN a.resource_type = 'audit_event' THEN 'audit'
               WHEN a.resource_type = 'work_session' AND a.action NOT IN {OPERATOR_ACTIONS + ("read",)} THEN 'session'
               ELSE 'web'
           END AS source_service
    FROM access_audit a
    UNION ALL
    SELECT e.id, 'count_event'::text, e.occurred_at, CASE WHEN e.user_id IS NULL THEN 'system' ELSE 'user' END,
           e.user_id, t.code, 'work_session', e.session_id, o.institution_id,
           CASE WHEN t.code = 'close_blocked' THEN 'blocked' ELSE 'success' END, NULL::varchar, NULL::text,
           e.session_id, e.expected_quantity, e.detected_quantity, e.family_id, e.payload,
           'operation',
           CASE WHEN t.code IN ('instruments_added', 'session_close') THEN 'session' ELSE 'web' END
    FROM count_event e
    JOIN cat_event_type t ON t.id = e.event_type_id
    JOIN work_session w ON w.id = e.session_id
    JOIN operation o ON o.id = w.operation_id
)
"""

# Readable label of the record an event is about (same labels the RF audit screens showed).
RESOURCE_LABEL = """
CASE ev.resource_type
    WHEN 'work_session' THEN 'WS-' || upper(left(replace(ev.resource_id::text, '-', ''), 8))
    WHEN 'instrument_family' THEN (SELECT f.code FROM instrument_family f WHERE f.id = ev.resource_id)
    WHEN 'instrument' THEN (SELECT COALESCE(i.internal_code, i.id::text) FROM instrument i WHERE i.id = ev.resource_id)
    WHEN 'kit' THEN (SELECT k.name || ' v' || k.version FROM kit k WHERE k.id = ev.resource_id)
    WHEN 'procedure' THEN (SELECT p.code FROM cat_procedure_type p WHERE p.id = ev.resource_id)
    WHEN 'user' THEN (SELECT us.email FROM "user" us WHERE us.id = ev.resource_id)
    WHEN 'role' THEN (SELECT r.code FROM role r WHERE r.id = ev.resource_id)
    WHEN 'vision_model' THEN (SELECT y.version_tag FROM yolo_model y WHERE y.id = ev.resource_id)
    WHEN 'operating_room' THEN (SELECT o.code FROM operating_room o WHERE o.id = ev.resource_id)
    WHEN 'capture_station' THEN (SELECT c.name FROM capture_station c WHERE c.id = ev.resource_id)
    WHEN 'institution' THEN (SELECT n.name FROM institution n WHERE n.id = ev.resource_id)
END
"""

SELECT_COLUMNS = f"""
    ev.*, u.name AS user_name, u.email AS user_email, f.code AS family_code,
    (SELECT string_agg(r.code, ',' ORDER BY r.code) FROM user_role ur JOIN role r ON r.id = ur.role_id
      WHERE ur.user_id = ev.user_id AND r.institution_id = ev.institution_id) AS role_codes,
    {RESOURCE_LABEL} AS resource_label
"""
JOINS = 'LEFT JOIN "user" u ON u.id = ev.user_id LEFT JOIN instrument_family f ON f.id = ev.family_id'

FILTER_COLUMNS = {
    "user_id": "ev.user_id = :user_id",
    "action": "ev.action = :action",
    "category": "ev.category = :category",
    "outcome": "ev.outcome = :outcome",
    "source_service": "ev.source_service = :source_service",
    "record_type": "ev.record_type = :record_type",
    "resource_type": "ev.resource_type = :resource_type",
    "session_id": "ev.session_id = :session_id",
    "correlation_id": "ev.correlation_id = :correlation_id",
    "date_from": "ev.occurred_at >= :date_from",
    "date_to": "ev.occurred_at < :date_to",
}
SEARCH = ("(ev.action ILIKE :q OR ev.resource_type ILIKE :q OR u.name ILIKE :q OR u.email ILIKE :q "
          "OR ev.resource_id::text ILIKE :q OR ev.correlation_id ILIKE :q "
          "OR ('WS-' || upper(left(replace(ev.resource_id::text, '-', ''), 8))) ILIKE :q)")


def _where(scope: str, institution_id: uuid.UUID, filters: dict[str, Any]) -> tuple[str, dict[str, Any], bool]:
    conditions = ["ev.institution_id = :institution_id"]
    params: dict[str, Any] = {"institution_id": institution_id}
    expanding = False
    if scope != "admin":
        # The SPD Supervisor sees the operational trail of the institution, not identity administration.
        conditions.append("ev.resource_type IN :supervisor_resources")
        params["supervisor_resources"] = list(SUPERVISOR_RESOURCES)
        expanding = True
    for name, clause in FILTER_COLUMNS.items():
        if filters.get(name) is not None:
            conditions.append(clause)
            params[name] = filters[name]
    if filters.get("q"):
        conditions.append(SEARCH)
        params["q"] = f"%{filters['q']}%"
    return " AND ".join(conditions), params, expanding


def _statement(sql: str, expanding: bool):
    statement = text(sql)
    if expanding:
        statement = statement.bindparams(bindparam("supervisor_resources", expanding=True))
    return statement


def search(connection: Connection, scope: str, institution_id: uuid.UUID, filters: dict[str, Any], *,
           descending: bool, limit: int, offset: int) -> tuple[int, list[dict[str, Any]]]:
    where, params, expanding = _where(scope, institution_id, filters)
    total = connection.execute(_statement(
        f"{EVENTS_CTE} SELECT count(*) FROM events ev {JOINS} WHERE {where}", expanding), params).scalar_one()
    order = "DESC" if descending else "ASC"
    rows = connection.execute(_statement(
        f"{EVENTS_CTE} SELECT {SELECT_COLUMNS} FROM events ev {JOINS} WHERE {where} "
        f"ORDER BY ev.occurred_at {order}, ev.id {order} LIMIT :limit OFFSET :offset", expanding),
        {**params, "limit": limit, "offset": offset}).mappings().all()
    return int(total), [dict(row) for row in rows]


def get(connection: Connection, scope: str, institution_id: uuid.UUID, event_id: uuid.UUID) -> dict[str, Any] | None:
    where, params, expanding = _where(scope, institution_id, {})
    row = connection.execute(_statement(
        f"{EVENTS_CTE} SELECT {SELECT_COLUMNS} FROM events ev {JOINS} WHERE {where} AND ev.id = :event_id",
        expanding), {**params, "event_id": event_id}).mappings().first()
    return dict(row) if row else None


def summary(connection: Connection, scope: str, institution_id: uuid.UUID, filters: dict[str, Any]) -> dict[str, Any]:
    where, params, expanding = _where(scope, institution_id, filters)
    base = f"{EVENTS_CTE} SELECT {{columns}} FROM events ev {JOINS} WHERE {where} {{tail}}"

    def run(columns: str, tail: str = ""):
        return connection.execute(_statement(base.format(columns=columns, tail=tail), expanding), params).all()

    totals = run("count(*), min(ev.occurred_at), max(ev.occurred_at)")[0]

    def group(column: str):
        return [{"value": value, "count": int(count)}
                for value, count in run(f"{column}, count(*)", f"GROUP BY {column} ORDER BY count(*) DESC, {column}")]

    users = run("ev.user_id, u.name, u.email, count(*)",
                "AND ev.user_id IS NOT NULL GROUP BY ev.user_id, u.name, u.email ORDER BY u.name, ev.user_id")
    sessions = run("ev.session_id, max(ev.occurred_at), count(*)",
                   "AND ev.session_id IS NOT NULL GROUP BY ev.session_id ORDER BY max(ev.occurred_at) DESC LIMIT 200")
    return {
        "total": int(totals[0]), "first_at": totals[1], "last_at": totals[2],
        "by_category": group("ev.category"), "by_source_service": group("ev.source_service"),
        "by_outcome": group("ev.outcome"), "by_record_type": group("ev.record_type"),
        "by_action": group("ev.action"), "by_resource_type": group("ev.resource_type"),
        "by_user": [{"user_id": row[0], "name": row[1], "email": row[2], "count": int(row[3])} for row in users],
        "by_session": [{"session_id": row[0], "last_at": row[1], "count": int(row[2])} for row in sessions],
    }
