"""MS-12 (ms-integracion-hospitalaria): traceable batch exports ("lotes") of CLOSED counting sessions.

- Who: the institution's IT administrator (user) or a machine client with scope `export.sessions.batch`.
  Always the caller's institution (never from the body).
- What: the closed sessions (status `closed`) whose `ended_at` falls in [period_from, period_to), at most
  EXPORT_MAX_SESSIONS. One document in PEF's own documented format (`pef-session-batch/1`, JSON or XML): session,
  Encounter reference (operation id), room code, kit version, tray code, capture mode, pinned model, timestamps,
  expected / final / validated totals, discrepancies (total / unresolved) and the integrity seal of the validation.
  NO patient data, NO physicians, NO images: a counting session is not presented as a clinical event (the semantic
  approval of a clinical mapping is pending: DS08 / decision D4).
- Traceability: the EXACT bytes are stored with their SHA-256, the items (batch -> sessions), who / when, and an
  access_audit row (EXPORT_SESSION_BATCH). A batch is immutable (trigger of migration 019); downloading it returns the
  same bytes (and is audited: DOWNLOAD_SESSION_BATCH). The same session can appear in several batches (a batch is
  a delivery record, not a lock).
- Nothing is sent anywhere: delivering the file to a hospital system is an institutional decision (D4).
"""

from __future__ import annotations

import hashlib
import json
import uuid
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import text

from auth import Principal
from errors import ApiError
from services import audit

FORMAT = "pef-session-batch/1"
EXPORT_MAX_SESSIONS = 2000
EXPORT_MAX_DAYS = 366

_SESSIONS = text("""
SELECT w.id, w.operation_id, w.started_at, w.ended_at, w.capture_mode, r.code AS room_code,
       k.id AS kit_id, k.name AS kit_name, k.version AS kit_version, t.code AS tray_code,
       m.version_tag AS model_version, m.checksum AS model_sha256,
       (SELECT COALESCE(sum(e.expected_quantity), 0) FROM expected_inventory e WHERE e.session_id = w.id) AS expected_total,
       (SELECT ce.detected_quantity FROM count_event ce WHERE ce.session_id = w.id AND ce.payload->>'source' = 'last_frame'
          ORDER BY ce.occurred_at DESC LIMIT 1) AS final_total,
       (SELECT ce.payload->'validation_seal'->>'sha256' FROM count_event ce JOIN cat_event_type et ON et.id = ce.event_type_id
          WHERE ce.session_id = w.id AND et.code = 'manual_close' AND ce.user_id IS NOT NULL
          ORDER BY ce.occurred_at DESC LIMIT 1) AS validation_seal_sha256,
       (SELECT count(*) FROM discrepancy d WHERE d.session_id = w.id) AS discrepancies_total,
       (SELECT count(*) FROM discrepancy d WHERE d.session_id = w.id AND NOT d.resolved) AS discrepancies_unresolved
FROM work_session w
JOIN operation o ON o.id = w.operation_id AND o.institution_id = :inst
JOIN cat_session_status s ON s.id = w.status_id AND s.code = 'closed'
LEFT JOIN operating_room r ON r.id = o.room_id
LEFT JOIN kit k ON k.id = w.kit_id
LEFT JOIN surgical_tray t ON t.id = w.tray_id
LEFT JOIN yolo_model m ON m.id = w.yolo_model_id
WHERE w.ended_at >= :from AND w.ended_at < :to
ORDER BY w.ended_at, w.id
LIMIT :limit
""")
_VALIDATED = text("""
SELECT ce.session_id, sum(ce.detected_quantity) AS total FROM count_event ce JOIN cat_event_type et ON et.id = ce.event_type_id
WHERE et.code = 'manual_count' AND ce.session_id = ANY(:ids)
  AND ce.occurred_at = (SELECT max(c2.occurred_at) FROM count_event c2 WHERE c2.session_id = ce.session_id
                        AND c2.event_type_id = ce.event_type_id)
GROUP BY ce.session_id
""")


def _iso(value):
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z") if value else None


def _period(raw: Any, field: str) -> datetime:
    try:
        value = datetime.fromisoformat(str(raw or "").replace("Z", "+00:00"))
    except ValueError:
        raise ApiError.validation_error(f"{field} must be an ISO 8601 date-time", details={"field": field}) from None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _xml(document: dict[str, Any]) -> bytes:
    root = ET.Element("session_batch")

    def fill(element, value):
        if value is None:
            element.set("nil", "true")
        elif isinstance(value, bool):
            element.text = "true" if value else "false"
        elif isinstance(value, dict):
            for key, child in value.items():
                fill(ET.SubElement(element, key), child)
        elif isinstance(value, list):
            for child in value:
                fill(ET.SubElement(element, "session"), child)
        else:
            element.text = str(value)
    fill(root, document)
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def _summary(row) -> dict[str, Any]:
    return {"id": str(row["id"]), "format": row["format"], "period_from": _iso(row["period_from"]),
            "period_to": _iso(row["period_to"]), "sessions_count": row["sessions_count"],
            "content_sha256": row["content_sha256"], "size_bytes": row["size_bytes"],
            "created_by_user_id": str(row["created_by_user_id"]) if row["created_by_user_id"] else None,
            "created_by_client_id": str(row["created_by_client_id"]) if row["created_by_client_id"] else None,
            "created_at": _iso(row["created_at"])}


class ExportService:
    def __init__(self, database):
        self.database = database

    def create(self, principal: Principal, body: dict[str, Any]) -> dict[str, Any]:
        fmt = str(body.get("format") or "json").lower()
        if fmt not in ("json", "xml"):
            raise ApiError.validation_error("format must be json or xml", details={"field": "format"})
        start, end = _period(body.get("period_from"), "period_from"), _period(body.get("period_to"), "period_to")
        if start >= end:
            raise ApiError.validation_error("period_from must be before period_to", details={"field": "period_to"})
        if (end - start).days > EXPORT_MAX_DAYS:
            raise ApiError.validation_error(f"The period is limited to {EXPORT_MAX_DAYS} days", details={"field": "period_to"})
        if end > datetime.now(timezone.utc):
            raise ApiError.validation_error("period_to cannot be in the future (sessions may still close)",
                                            details={"field": "period_to"})
        batch_id = uuid.uuid4()
        with self.database.begin() as connection:
            rows = connection.execute(_SESSIONS, {"inst": principal.institution_id, "from": start, "to": end,
                                                  "limit": EXPORT_MAX_SESSIONS + 1}).mappings().all()
            if len(rows) > EXPORT_MAX_SESSIONS:
                raise ApiError.validation_error(f"More than {EXPORT_MAX_SESSIONS} sessions: split the period",
                                                details={"field": "period_to", "reason": "too_many_sessions"})
            if not rows:
                raise ApiError(422, "No closed session in this period", error="empty_batch",
                               details={"field": "period_from"})
            validated = {r["session_id"]: int(r["total"]) for r in connection.execute(
                _VALIDATED, {"ids": [r["id"] for r in rows]}).mappings()}
            generated_at = datetime.now(timezone.utc)
            document = {
                "format": FORMAT, "batch_id": str(batch_id), "institution_id": str(principal.institution_id),
                "generated_at": _iso(generated_at), "period_from": _iso(start), "period_to": _iso(end),
                "note": "Counting traceability only; no patient data, no clinical mapping (DS08).",
                "sessions": [{
                    "session_id": str(r["id"]), "encounter_id": str(r["operation_id"]), "room_code": r["room_code"],
                    "kit_id": str(r["kit_id"]) if r["kit_id"] else None, "kit_name": r["kit_name"],
                    "kit_version": r["kit_version"], "tray_code": r["tray_code"], "capture_mode": r["capture_mode"],
                    "model_version": r["model_version"], "model_sha256": (r["model_sha256"] or "").lower() or None,
                    "started_at": _iso(r["started_at"]), "ended_at": _iso(r["ended_at"]),
                    "expected_total": int(r["expected_total"]),
                    "final_count_total": r["final_total"], "validated_total": validated.get(r["id"]),
                    "discrepancies_total": int(r["discrepancies_total"]),
                    "discrepancies_unresolved": int(r["discrepancies_unresolved"]),
                    "validation_seal_sha256": r["validation_seal_sha256"]} for r in rows],
            }
            content = (json.dumps(document, ensure_ascii=False, indent=2).encode("utf-8") if fmt == "json"
                       else _xml(document))
            sha = hashlib.sha256(content).hexdigest()
            connection.execute(text("""
                INSERT INTO integration_export_batch (id, institution_id, format, period_from, period_to, sessions_count,
                    content, content_sha256, created_by_user_id, created_by_client_id, created_at)
                VALUES (:id, :inst, :fmt, :from, :to, :n, :content, :sha, :user, :client, :at)"""),
                {"id": batch_id, "inst": principal.institution_id, "fmt": fmt, "from": start, "to": end, "n": len(rows),
                 "content": content, "sha": sha, "user": principal.id if principal.is_user else None,
                 "client": None if principal.is_user else principal.id, "at": generated_at})
            connection.execute(text("INSERT INTO integration_export_item (batch_id, session_id) VALUES (:b, :s)"),
                               [{"b": batch_id, "s": r["id"]} for r in rows])
            audit.record(connection, principal, "EXPORT_SESSION_BATCH", "integration_export_batch", batch_id)
        return self.get(principal, batch_id)

    def list(self, principal: Principal, args) -> list[dict[str, Any]]:
        unknown = sorted(set(args) - {"session_id"})
        if unknown:
            raise ApiError.invalid_request("Unknown query parameter(s)", details={"unknown_parameters": unknown})
        params: dict[str, Any] = {"inst": principal.institution_id}
        where = ""
        if args.get("session_id"):
            try:
                params["session"] = uuid.UUID(args["session_id"])
            except ValueError:
                raise ApiError.invalid_request("session_id must be a UUID", details={"field": "session_id"}) from None
            where = " AND EXISTS (SELECT 1 FROM integration_export_item i WHERE i.batch_id = b.id AND i.session_id = :session)"
        with self.database.connect() as connection:
            rows = connection.execute(text(
                "SELECT b.id, b.format, b.period_from, b.period_to, b.sessions_count, b.content_sha256, "
                "octet_length(b.content) AS size_bytes, b.created_by_user_id, b.created_by_client_id, b.created_at "
                "FROM integration_export_batch b WHERE b.institution_id = :inst" + where +
                " ORDER BY b.created_at DESC LIMIT 200"), params).mappings().all()
        return [_summary(r) for r in rows]

    def get(self, principal: Principal, batch_id: uuid.UUID) -> dict[str, Any]:
        with self.database.connect() as connection:
            row = connection.execute(text(
                "SELECT b.id, b.format, b.period_from, b.period_to, b.sessions_count, b.content_sha256, "
                "octet_length(b.content) AS size_bytes, b.created_by_user_id, b.created_by_client_id, b.created_at "
                "FROM integration_export_batch b WHERE b.id = :id AND b.institution_id = :inst"),
                {"id": batch_id, "inst": principal.institution_id}).mappings().first()
            if row is None:
                raise ApiError.not_found("Export batch not found")
            sessions = connection.execute(text("SELECT session_id FROM integration_export_item WHERE batch_id = :b "
                                               "ORDER BY session_id"), {"b": batch_id}).scalars().all()
        return {**_summary(row), "session_ids": [str(s) for s in sessions]}

    def content(self, principal: Principal, batch_id: uuid.UUID) -> tuple[bytes, str, str]:
        with self.database.begin() as connection:
            row = connection.execute(text("SELECT content, content_sha256, format FROM integration_export_batch "
                                          "WHERE id = :id AND institution_id = :inst"),
                                     {"id": batch_id, "inst": principal.institution_id}).mappings().first()
            if row is None:
                raise ApiError.not_found("Export batch not found")
            audit.record(connection, principal, "DOWNLOAD_SESSION_BATCH", "integration_export_batch", batch_id)
        return bytes(row["content"]), row["content_sha256"], row["format"]
