"""ELECTRON-01: read-only station monitor for the desktop client (apps/PEF-Electron).

The desktop monitor is a *viewer*: it never creates, starts, validates or closes a session. It asks
three questions, all answered from PostgreSQL (the single source of truth) under the usual JWT rules:

1. ``station_overview``  — Which session is active (``in_progress``) at my station right now?
2. ``session_monitor``   — What is the official state of the session I was following (status, phase,
                           current reference, official validation result)?
3. ``live_ticket``       — Give me a short-lived, *observe-only* ticket for the VisionWorker WebSocket
                           so I can watch the detections the Operator's capture is already producing.

Authorisation (same model as the rest of this service, never trusting ids sent by the client):
- role ``station_operator`` (checked by the route decorator);
- the station must belong to an operating room of the caller's institution (otherwise 404, never a
  hint that it exists elsewhere) and be active (409 ``station_disabled``);
- a session is visible only if it is at that station, in the caller's institution and assigned to the
  caller — exactly the "operator: own sessions" read rule of this service.

Nothing here writes: no audit rows, no events, no state changes.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import time
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from auth import Principal
from database import Database
from errors import ApiError

logger = logging.getLogger("session.station_monitor")

# Statuses in which the Operator is still capturing at the station (the only "active" one).
ACTIVE_STATUS = "in_progress"
# Observe-only ticket: a different MAC domain from the portal's frame-submit tickets, so one can never
# be replayed as the other even if both secrets were (mis)configured equal.
MONITOR_TICKET_PREFIX = "m1"
MONITOR_TICKET_DOMAIN = "pef-monitor:v1"


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.isoformat()


def _uuid(raw: Any, *, what: str) -> uuid.UUID:
    try:
        return uuid.UUID(str(raw))
    except (TypeError, ValueError):
        raise ApiError.not_found(f"{what} not found") from None


def issue_monitor_ticket(session_id: str, *, secret: str, ttl_s: int, now: float | None = None) -> dict[str, Any]:
    """``m1.<session>.<exp>.<nonce>.<hmac>`` — verified by VisionWorker (monitor_ticket.py)."""
    exp = int(now if now is not None else time.time()) + int(ttl_s)
    nonce = uuid.uuid4().hex
    message = f"{MONITOR_TICKET_DOMAIN}:{session_id}:{exp}:{nonce}".encode("utf-8")
    signature = hmac.new(secret.encode("utf-8"), message, hashlib.sha256).hexdigest()
    return {
        "ticket": f"{MONITOR_TICKET_PREFIX}.{session_id}.{exp}.{nonce}.{signature}",
        "expires_at": exp,
        "ttl_s": int(ttl_s),
    }


class StationMonitorService:
    def __init__(self, database: Database, *, monitor_secret: str = "", ticket_ttl_s: int = 60, ws_url: str = ""):
        self.database = database
        self.monitor_secret = monitor_secret or ""
        self.ticket_ttl_s = max(20, min(int(ticket_ttl_s or 60), 300))
        self.ws_url = ws_url or ""

    # --- Station ---------------------------------------------------------------------------

    def _station(self, connection, principal: Principal, raw_station_id: str) -> dict[str, Any]:
        station_id = _uuid(raw_station_id, what="Station")
        row = connection.execute(text("""
            SELECT s.id, s.name, s.active, r.id AS room_id, r.code AS room_code, r.name AS room_name,
                   r.active AS room_active, r.institution_id
            FROM capture_station s JOIN operating_room r ON r.id = s.room_id
            WHERE s.id = :id
        """), {"id": station_id}).mappings().first()
        # Another institution's station is indistinguishable from a missing one.
        if row is None or row["institution_id"] != principal.institution_id:
            raise ApiError.not_found("Station not found")
        if not row["active"] or not row["room_active"]:
            raise ApiError(409, "Station is disabled", error="station_disabled",
                           details={"station_id": str(station_id)})
        return dict(row)

    @staticmethod
    def _optional(connection, sql: str, params: dict[str, Any]) -> list[dict[str, Any]] | None:
        """Queries on MS-05 tables (migration 012): None when they do not exist in this database."""
        try:
            with connection.begin_nested():
                return [dict(r) for r in connection.execute(text(sql), params).mappings().all()]
        except SQLAlchemyError:
            logger.warning("station monitor: optional camera tables unavailable")
            return None

    def _cameras(self, connection, station_id: uuid.UUID) -> dict[str, Any]:
        cameras = self._optional(connection, """
            SELECT id, name, device_type, active FROM capture_camera
            WHERE station_id = :s ORDER BY active DESC, name
        """, {"s": station_id})
        report = self._optional(connection, """
            SELECT camera_reported, camera_reported_at, frames_seen_at, last_seen_at, client
            FROM station_status WHERE station_id = :s
        """, {"s": station_id})
        last = (report or [None])[0]
        return {
            "registry_available": cameras is not None,
            "cameras": [
                {
                    "id": str(c["id"]),
                    "name": c["name"],
                    "device_type": c["device_type"],
                    "simulated": c["device_type"] == "simulated",
                    "active": bool(c["active"]),
                }
                for c in (cameras or [])
            ],
            # What a client last *reported* and when VisionWorker last saw frames: never inferred here.
            "last_report": None if last is None else {
                "camera_reported": last["camera_reported"],
                "camera_reported_at": _iso(last["camera_reported_at"]),
                "frames_seen_at": _iso(last["frames_seen_at"]),
                "last_seen_at": _iso(last["last_seen_at"]),
                "client": last["client"],
            },
        }

    # --- Session ---------------------------------------------------------------------------

    _SESSION_SQL = """
        SELECT w.id, w.station_id, w.user_id, w.capture_mode, w.started_at, w.ended_at, w.phase_changed_at,
               w.updated_at, st.code AS status_code, st.name AS status_name,
               ph.code AS phase_code, ph.name AS phase_name, pt.code AS procedure_code,
               pt.name AS procedure_name,
               (SELECT p.display_name FROM operation_patient op
                JOIN patient p ON p.id = op.patient_id AND p.institution_id = r.institution_id
                WHERE op.operation_id = o.id ORDER BY op.id LIMIT 1) AS patient_name,
               (SELECT physician.name FROM operation_physician op
                JOIN physician ON physician.id = op.physician_id
                    AND physician.institution_id = r.institution_id
                JOIN cat_surgical_role role ON role.id = op.surgical_role_id
                WHERE op.operation_id = o.id
                ORDER BY (role.code = 'surgeon') DESC, physician.name, physician.id
                LIMIT 1) AS doctor_name
        FROM work_session w
        JOIN cat_session_status st ON st.id = w.status_id
        JOIN capture_station s ON s.id = w.station_id
        JOIN operating_room r ON r.id = s.room_id
        LEFT JOIN cat_operation_phase ph ON ph.id = w.current_phase_id
        LEFT JOIN operation o ON o.id = w.operation_id
        LEFT JOIN cat_procedure_type pt ON pt.id = o.procedure_type_id
        WHERE w.station_id = :station AND r.institution_id = :institution AND w.user_id = :user
    """

    def _reference(self, connection, session_id: uuid.UUID) -> dict[str, Any]:
        rows = connection.execute(text("""
            SELECT ei.family_id, f.code AS family_code, f.name AS family_name, ei.expected_quantity
            FROM expected_inventory ei JOIN instrument_family f ON f.id = ei.family_id
            WHERE ei.session_id = :s ORDER BY f.name, f.code
        """), {"s": session_id}).mappings().all()
        families = [
            {
                "family_id": str(r["family_id"]),
                "family_code": r["family_code"],
                "family_name": r["family_name"],
                "expected_quantity": int(r["expected_quantity"]),
            }
            for r in rows
        ]
        canonical = "\n".join(sorted(f"{f['family_id']}:{f['expected_quantity']}" for f in families))
        return {
            "total": sum(f["expected_quantity"] for f in families),
            # Lets the client notice a reference change (e.g. instruments added by the Supervisor).
            "version": hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16],
            "families": families,
        }

    def _official(self, connection, session_id: uuid.UUID, status_code: str) -> dict[str, Any]:
        disc = connection.execute(text("""
            SELECT count(*) AS total, count(*) FILTER (WHERE NOT resolved) AS open
            FROM discrepancy WHERE session_id = :s
        """), {"s": session_id}).mappings().first()
        close = connection.execute(text("""
            SELECT ce.occurred_at, ce.payload
            FROM count_event ce JOIN cat_event_type t ON t.id = ce.event_type_id
            WHERE ce.session_id = :s AND t.code = 'manual_close'
            ORDER BY ce.occurred_at DESC LIMIT 1
        """), {"s": session_id}).mappings().first()
        validated = close is not None
        payload = (close or {}).get("payload") or {}
        open_d, total_d = int(disc["open"] or 0), int(disc["total"] or 0)
        # Mirrors the official outcome only; no new rule is derived here.
        if status_code == "aborted":
            result = "aborted"
        elif status_code == "closed":
            result = "closed_clean" if total_d == 0 else "closed_with_discrepancies"
        elif open_d > 0 or status_code in {"correction_required", "or_hold"}:
            result = "discrepancy"
        elif validated and status_code == "awaiting_spd_review":
            result = "validated_match"
        else:
            result = "pending"
        return {
            "result": result,
            "validated": validated,
            "validated_at": _iso(close["occurred_at"]) if close else None,
            "close_path": payload.get("close_path") if isinstance(payload, dict) else None,
            "open_discrepancies": open_d,
            "discrepancies_total": total_d,
        }

    def _session_camera(self, connection, session_id: uuid.UUID) -> dict[str, Any] | None:
        rows = self._optional(connection, """
            SELECT c.id, c.name, c.device_type FROM work_session w
            JOIN capture_camera c ON c.id = w.camera_id WHERE w.id = :s
        """, {"s": session_id})
        if not rows:
            return None
        cam = rows[0]
        return {"id": str(cam["id"]), "name": cam["name"], "device_type": cam["device_type"],
                "simulated": cam["device_type"] == "simulated"}

    def _snapshot(self, connection, row: dict[str, Any]) -> dict[str, Any]:
        session_id = row["id"]
        status_code = row["status_code"]
        return {
            "session_id": str(session_id),
            "session_ref": str(session_id)[:8].upper(),
            "station_id": str(row["station_id"]),
            "status_code": status_code,
            "status_name": row["status_name"],
            "phase_code": row["phase_code"],
            "phase_name": row["phase_name"],
            "phase_changed_at": _iso(row["phase_changed_at"]),
            "capture_mode": row["capture_mode"],
            "procedure_name": row["procedure_name"],
            "procedure_code": row["procedure_code"],
            "patient_name": row["patient_name"],
            "doctor_name": row["doctor_name"],
            "started_at": _iso(row["started_at"]),
            "ended_at": _iso(row["ended_at"]),
            "updated_at": _iso(row["updated_at"]),
            "camera": self._session_camera(connection, session_id),
            "reference": self._reference(connection, session_id),
            "official": self._official(connection, session_id, status_code),
            "live_available": bool(
                status_code == ACTIVE_STATUS and row["capture_mode"] == "vision" and self.monitor_secret
            ),
        }

    # --- Endpoints -------------------------------------------------------------------------

    def station_overview(self, principal: Principal, raw_station_id: str) -> dict[str, Any]:
        with self.database.connect() as connection:
            station = self._station(connection, principal, raw_station_id)
            active = connection.execute(text(self._SESSION_SQL + """
                AND st.code = :active ORDER BY w.started_at DESC NULLS LAST, w.id
            """), {"station": station["id"], "institution": principal.institution_id,
                   "user": principal.user_id, "active": ACTIVE_STATUS}).mappings().all()
            cameras = self._cameras(connection, station["id"])
            if len(active) == 1:
                state, session, candidates = "active", self._snapshot(connection, dict(active[0])), []
            elif len(active) > 1:
                # Never pick one arbitrarily: the Operator must leave only one in progress from the portal.
                state, session = "ambiguous", None
                candidates = [{"session_id": str(r["id"]), "session_ref": str(r["id"])[:8].upper(),
                               "started_at": _iso(r["started_at"])} for r in active]
            else:
                state, session, candidates = "idle", None, []
        return {
            "server_time": datetime.now(timezone.utc).isoformat(),
            "station": {
                "id": str(station["id"]),
                "name": station["name"],
                "room_code": station["room_code"],
                "room_name": station["room_name"],
            },
            "camera_registry": cameras,
            "state": state,
            "session": session,
            "candidates": candidates,
            "live_monitor_enabled": bool(self.monitor_secret),
        }

    def _owned_session(self, connection, principal: Principal, station: dict[str, Any], raw_session_id: str):
        session_id = _uuid(raw_session_id, what="Session")
        row = connection.execute(text(self._SESSION_SQL + " AND w.id = :id"), {
            "station": station["id"], "institution": principal.institution_id,
            "user": principal.user_id, "id": session_id,
        }).mappings().first()
        if row is None:
            # Other station, other institution, other Operator or unknown: all the same 404.
            raise ApiError.not_found("Session not found")
        return dict(row)

    def session_monitor(self, principal: Principal, raw_station_id: str, raw_session_id: str) -> dict[str, Any]:
        with self.database.connect() as connection:
            station = self._station(connection, principal, raw_station_id)
            row = self._owned_session(connection, principal, station, raw_session_id)
            snapshot = self._snapshot(connection, row)
        return {"server_time": datetime.now(timezone.utc).isoformat(), "session": snapshot}

    def live_ticket(self, principal: Principal, raw_station_id: str, raw_session_id: str) -> dict[str, Any]:
        if not self.monitor_secret:
            raise ApiError(503, "Live monitor is not configured", error="monitor_unavailable",
                           details={"dependency": "vision_monitor"})
        with self.database.connect() as connection:
            station = self._station(connection, principal, raw_station_id)
            row = self._owned_session(connection, principal, station, raw_session_id)
        if row["status_code"] != ACTIVE_STATUS or row["capture_mode"] != "vision":
            raise ApiError(409, "Live monitor is only available while a vision session is in progress",
                           error="live_not_available",
                           details={"status_code": row["status_code"], "capture_mode": row["capture_mode"]})
        issued = issue_monitor_ticket(str(row["id"]), secret=self.monitor_secret, ttl_s=self.ticket_ttl_s)
        return {"session_id": str(row["id"]), "ws_url": self.ws_url or None, "scope": "observe", **issued}
