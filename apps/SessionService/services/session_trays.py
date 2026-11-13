"""MS-08: physical tray of a session (work_session.tray_id, migration 015).

- Optional when scheduling. When given it must be a tray of the caller's institution, with status `active`,
  and configured with the SAME kit version as the session (or with no kit). Otherwise 422 (invalid_tray /
  inactive_tray / tray_kit_mismatch). A tray configured with another kit is refused instead of silently
  changing the kit: the expected quantities of the session come from the session's kit and stay frozen.
- Two sessions using the same tray at the same time are NOT prevented: the RF does not define the tray
  lifecycle (pending decision), so nothing is inferred.
- Read: the session detail shows its tray; `GET /api/v1/sessions/trays/{tray_id}` lists the sessions that used
  a tray (Supervisor). Changing the tray afterwards (in CatalogService) never changes any session.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import text

from errors import ApiError


def _error(reason: str, message: str) -> ApiError:
    return ApiError(422, message, error="validation_error", details={"reason": reason, "field": "tray_id"})


def validate(connection, payload: dict[str, Any], tenant: uuid.UUID, kit_id: uuid.UUID) -> uuid.UUID | None:
    raw = payload.get("tray_id")
    if raw in (None, ""):
        return None
    try:
        tray_id = uuid.UUID(str(raw))
    except (TypeError, ValueError):
        raise _error("invalid_tray", "Choose a valid tray") from None
    # MS-09: FOR SHARE: a concurrent retirement / kit change of the tray waits for this scheduling (and vice versa).
    row = connection.execute(text("SELECT status, kit_id FROM surgical_tray WHERE id = :id AND institution_id = :inst "
                                  "FOR SHARE"), {"id": tray_id, "inst": tenant}).mappings().first()
    if row is None:
        raise _error("invalid_tray", "Choose a valid tray")
    if row["status"] != "active":
        raise _error("inactive_tray", "This tray is out of service or retired. Choose an active tray")
    if row["kit_id"] is not None and row["kit_id"] != kit_id:
        raise _error("tray_kit_mismatch", "This tray is configured with another kit version")
    return tray_id


def attach(connection, session_id: uuid.UUID, tray_id: uuid.UUID | None) -> None:
    if tray_id is not None:
        connection.execute(text("UPDATE work_session SET tray_id = :t WHERE id = :s"), {"t": tray_id, "s": session_id})


def current(connection, session_id: uuid.UUID) -> uuid.UUID | None:
    return connection.execute(text("SELECT tray_id FROM work_session WHERE id = :s"), {"s": session_id}).scalar()


def replace(connection, session_id: uuid.UUID, tray_id: uuid.UUID | None) -> None:
    """MS-09: change (or remove) the tray of a SCHEDULED session; expected quantities are not touched."""
    connection.execute(text("UPDATE work_session SET tray_id = :t WHERE id = :s"), {"t": tray_id, "s": session_id})


def of_session(connection, session_id: uuid.UUID) -> dict[str, Any] | None:
    row = connection.execute(text(
        "SELECT t.id, t.code, t.name, t.status FROM work_session w JOIN surgical_tray t ON t.id = w.tray_id "
        "WHERE w.id = :s"), {"s": session_id}).mappings().first()
    return {"id": str(row["id"]), "code": row["code"], "name": row["name"], "status": row["status"]} if row else None


def sessions_for_tray(database, principal, raw_tray_id: str) -> dict[str, Any]:
    try:
        tray_id = uuid.UUID(str(raw_tray_id))
    except (TypeError, ValueError):
        raise ApiError.not_found("Tray not found") from None
    with database.connect() as connection:
        tray = connection.execute(text("SELECT id, code FROM surgical_tray WHERE id = :id AND institution_id = :inst"),
                                  {"id": tray_id, "inst": principal.institution_id}).mappings().first()
        if not tray:
            raise ApiError.not_found("Tray not found")
        rows = connection.execute(text("""
            SELECT w.id, st.code AS status, o.scheduled_at, w.started_at, w.ended_at, w.kit_id, k.name AS kit_name,
                   k.version AS kit_version, u.name AS operator_name
            FROM work_session w
            JOIN operation o ON o.id = w.operation_id AND o.institution_id = :inst
            JOIN cat_session_status st ON st.id = w.status_id
            LEFT JOIN kit k ON k.id = w.kit_id
            LEFT JOIN "user" u ON u.id = w.user_id
            WHERE w.tray_id = :tray ORDER BY o.scheduled_at DESC NULLS LAST, w.id LIMIT 200
        """), {"tray": tray_id, "inst": principal.institution_id}).mappings().all()

    def iso(value):
        return value.isoformat().replace("+00:00", "Z") if value else None
    return {"tray_id": str(tray_id), "tray_code": tray["code"], "sessions": [{
        "id": str(r["id"]), "status": r["status"], "scheduled_at": iso(r["scheduled_at"]),
        "started_at": iso(r["started_at"]), "ended_at": iso(r["ended_at"]),
        "kit_id": str(r["kit_id"]) if r["kit_id"] else None, "kit_name": r["kit_name"], "kit_version": r["kit_version"],
        "operator_name": r["operator_name"]} for r in rows]}
