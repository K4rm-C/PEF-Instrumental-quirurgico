"""MS-05: capture stations and cameras — heartbeat, observed state and technical alerts.

Sources (migration 012):
- station_status.last_seen_at / camera_reported: the authenticated heartbeat of the station (desktop
  station client or the Operator's capture page through the portal). It proves the STATION is connected;
  what it says about the camera is only a report.
- station_status.frames_seen_at: written by the collector from VisionWorker's own live activity (frames it
  received for a session held at that station). It is the server-side evidence that the camera works.
- station_event: alerts (heartbeat_lost, camera_error) open until the matching recovery.

States:
- station: disabled (station or room inactive) · never_seen · online (heartbeat within station_stale_seconds)
  · offline (heartbeat older than that).
- camera: disabled (camera or station inactive) · error (reported error / unavailable by an online station)
  · ok (VisionWorker received its frames within camera_frames_seconds) · unverified (the station says ok but
  the server has not seen frames) · unknown (no information).

A station being online never makes its camera "ok": only frames observed by VisionWorker do.
Every query is limited to the caller's institution; another institution's station answers 404.
"""

from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import text

from database import Database
from errors import ApiError

CAMERA_REPORTS = ("ok", "error", "unavailable", "unknown")
HEARTBEAT_FIELDS = {"camera_id", "camera_status", "camera_error", "client"}
ERROR_CODE = re.compile(r"^[A-Za-z0-9_.:-]{1,120}$")
CLIENT_NAME = re.compile(r"^[A-Za-z0-9_.:/ -]{1,40}$")
OPEN_ALERT_KINDS = ("heartbeat_lost", "camera_error")
EVENT_LIMIT = 20
ALERT_LIMIT_MAX = 200


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _uuid(raw: Any, *, field: str | None = None) -> uuid.UUID:
    try:
        return uuid.UUID(str(raw))
    except (TypeError, ValueError):
        if field:
            raise ApiError.invalid_request("Invalid identifier", details={"field": field}) from None
        raise ApiError.not_found("Station not found") from None


def _age(now: datetime, value: datetime | None) -> float | None:
    return None if value is None else max(0.0, (now - value).total_seconds())


class StationService:
    def __init__(self, database: Database, stale_seconds: float, frames_seconds: float):
        self.database = database
        self.stale_seconds = stale_seconds
        self.frames_seconds = frames_seconds

    # --- heartbeat --------------------------------------------------------------------------------------

    @staticmethod
    def validate_heartbeat(payload: Any) -> dict[str, Any]:
        if not isinstance(payload, dict):
            raise ApiError.invalid_request("A JSON object is required")
        unknown = sorted(set(payload) - HEARTBEAT_FIELDS)
        if unknown:
            raise ApiError.invalid_request("Unknown fields", details={"unknown_fields": unknown})
        status = payload.get("camera_status", "unknown")
        if status not in CAMERA_REPORTS:
            raise ApiError.invalid_request("camera_status is not valid", details={"field": "camera_status",
                                                                                  "allowed": list(CAMERA_REPORTS)})
        error = payload.get("camera_error")
        if error is not None and (not isinstance(error, str) or not ERROR_CODE.match(error)):
            raise ApiError.invalid_request("camera_error must be a short code", details={"field": "camera_error"})
        client = payload.get("client")
        if client is not None and (not isinstance(client, str) or not CLIENT_NAME.match(client)):
            raise ApiError.invalid_request("client must be a short name", details={"field": "client"})
        camera = payload.get("camera_id")
        return {"camera_id": _uuid(camera, field="camera_id") if camera is not None else None,
                "camera_status": status, "camera_error": error if status in ("error", "unavailable") else None,
                "client": client}

    def heartbeat(self, principal, raw_station_id: str, payload: Any) -> dict[str, Any]:
        station_id = _uuid(raw_station_id)
        data = self.validate_heartbeat(payload)
        with self.database.begin() as connection:
            station = connection.execute(text("""
                SELECT s.id, s.active, r.active AS room_active, r.institution_id
                FROM capture_station s JOIN operating_room r ON r.id = s.room_id
                WHERE s.id = :id FOR UPDATE OF s
            """), {"id": station_id}).mappings().first()
            if station is None or station["institution_id"] != principal.institution_id:
                raise ApiError.not_found("Station not found")
            if not (station["active"] and station["room_active"]):
                raise ApiError.conflict("The station is disabled", details={"reason": "station_disabled"})
            if data["camera_id"] is not None:
                camera = connection.execute(text(
                    "SELECT station_id, active FROM capture_camera WHERE id = :id"), {"id": data["camera_id"]}).mappings().first()
                if camera is None or camera["station_id"] != station_id:
                    raise ApiError.invalid_reference("The camera does not belong to this station",
                                                     details={"field": "camera_id"})
                if not camera["active"]:
                    raise ApiError.conflict("The camera is disabled", details={"reason": "camera_disabled"})
            now = connection.execute(text("SELECT now()")).scalar_one()
            connection.execute(text("""
                INSERT INTO station_status (station_id, institution_id, last_seen_at, last_reported_by, client, camera_id,
                                            camera_reported, camera_error, camera_reported_at, updated_at)
                VALUES (:station, :inst, :now, :user, :client, :camera, :status, :error, :now, :now)
                ON CONFLICT (station_id) DO UPDATE SET
                    last_seen_at = EXCLUDED.last_seen_at, last_reported_by = EXCLUDED.last_reported_by,
                    client = EXCLUDED.client, camera_id = EXCLUDED.camera_id, camera_reported = EXCLUDED.camera_reported,
                    camera_error = EXCLUDED.camera_error, camera_reported_at = EXCLUDED.camera_reported_at,
                    updated_at = EXCLUDED.updated_at
            """), {"station": station_id, "inst": principal.institution_id, "now": now, "user": principal.user_id,
                   "client": data["client"], "camera": data["camera_id"], "status": data["camera_status"],
                   "error": data["camera_error"]})
            events = []
            if self._resolve(connection, station_id, "heartbeat_lost", now):
                events.append(self._event(connection, principal.institution_id, station_id, None, "heartbeat_recovered",
                                          {}, now))
            if data["camera_status"] in ("error", "unavailable"):
                created = self._open(connection, principal.institution_id, station_id, data["camera_id"], "camera_error",
                                     {"error": data["camera_error"] or data["camera_status"],
                                      "reported_by": str(principal.user_id)}, now)
                if created:
                    events.append(created)
            elif data["camera_status"] == "ok" and self._resolve(connection, station_id, "camera_error", now):
                events.append(self._event(connection, principal.institution_id, station_id, data["camera_id"],
                                          "camera_recovered", {"reported_by": str(principal.user_id)}, now))
        result = self.station(principal, str(station_id))
        result["events_recorded"] = events
        return result

    @staticmethod
    def _event(connection, institution_id, station_id, camera_id, kind, detail, now) -> str:
        connection.execute(text("""
            INSERT INTO station_event (institution_id, station_id, camera_id, kind, detail, occurred_at)
            VALUES (:inst, :station, :camera, :kind, CAST(:detail AS jsonb), :now)
        """), {"inst": institution_id, "station": station_id, "camera": camera_id, "kind": kind,
               "detail": json.dumps(detail), "now": now})
        return kind

    @staticmethod
    def _open(connection, institution_id, station_id, camera_id, kind, detail, now) -> str | None:
        """Opens an alert unless one of that kind is already open (partial unique index: never twice)."""
        inserted = connection.execute(text("""
            INSERT INTO station_event (institution_id, station_id, camera_id, kind, detail, occurred_at)
            VALUES (:inst, :station, :camera, :kind, CAST(:detail AS jsonb), :now)
            ON CONFLICT (station_id, kind) WHERE resolved_at IS NULL AND kind IN ('heartbeat_lost', 'camera_error')
            DO NOTHING
        """), {"inst": institution_id, "station": station_id, "camera": camera_id, "kind": kind,
               "detail": json.dumps(detail), "now": now}).rowcount
        return kind if inserted else None

    @staticmethod
    def _resolve(connection, station_id, kind, now) -> bool:
        return bool(connection.execute(text("""
            UPDATE station_event SET resolved_at = :now
            WHERE station_id = :station AND kind = :kind AND resolved_at IS NULL
        """), {"station": station_id, "kind": kind, "now": now}).rowcount)

    # --- reads (spd_supervisor / it_admin of the institution) -----------------------------------------------

    def _rows(self, connection, institution_id, station_id=None):
        params = {"inst": institution_id, "station": station_id}
        station_filter = "AND s.id = :station" if station_id else ""
        stations = connection.execute(text(f"""
            SELECT s.id, s.name, s.active, r.id AS room_id, r.code AS room_code, r.name AS room_name,
                   r.active AS room_active, st.last_seen_at, st.client, st.camera_id AS reported_camera_id,
                   st.camera_reported, st.camera_error, st.camera_reported_at, st.frames_seen_at,
                   st.frames_camera_id,
                   (SELECT count(*) FROM station_event e WHERE e.station_id = s.id AND e.resolved_at IS NULL
                     AND e.kind IN ('heartbeat_lost', 'camera_error'))
                       AS open_alerts
            FROM capture_station s
            JOIN operating_room r ON r.id = s.room_id
            LEFT JOIN station_status st ON st.station_id = s.id
            WHERE r.institution_id = :inst {station_filter}
            ORDER BY r.code, s.name
        """), params).mappings().all()
        cameras = connection.execute(text(f"""
            SELECT c.id, c.station_id, c.name, c.device_type, c.active, c.resolution_width, c.resolution_height, c.fps
            FROM capture_camera c JOIN capture_station s ON s.id = c.station_id
            WHERE c.institution_id = :inst {station_filter}
            ORDER BY c.name
        """), params).mappings().all()
        now = connection.execute(text("SELECT now()")).scalar_one()
        return stations, cameras, now

    def _station_view(self, row, cameras, now) -> dict[str, Any]:
        disabled = not (row["active"] and row["room_active"])
        seen_age = _age(now, row["last_seen_at"])
        if disabled:
            status = "disabled"
        elif row["last_seen_at"] is None:
            status = "never_seen"
        else:
            status = "online" if seen_age <= self.stale_seconds else "offline"
        frames_age = _age(now, row["frames_seen_at"])
        frames_recent = frames_age is not None and frames_age <= self.frames_seconds
        own = [c for c in cameras if c["station_id"] == row["id"]]
        camera_views = []
        for camera in own:
            reported = row["camera_reported"] if row["reported_camera_id"] == camera["id"] else None
            verified = frames_recent and (row["frames_camera_id"] == camera["id"]
                                          or (row["frames_camera_id"] is None and len(own) == 1))
            if disabled or not camera["active"]:
                camera_status = "disabled"
            elif status == "online" and reported in ("error", "unavailable"):
                camera_status = "error"
            elif verified:
                camera_status = "ok"
            elif status == "online" and reported == "ok":
                camera_status = "unverified"
            else:
                camera_status = "unknown"
            camera_views.append({
                "id": str(camera["id"]), "name": camera["name"], "device_type": camera["device_type"],
                "active": camera["active"], "status": camera_status, "reported_status": reported,
                "reported_at": _iso(row["camera_reported_at"]) if reported else None,
                "error": row["camera_error"] if reported in ("error", "unavailable") else None,
                "frames_seen_at": _iso(row["frames_seen_at"]) if verified else None,
                "verified_by_server": bool(verified),
                "resolution": (f'{camera["resolution_width"]}x{camera["resolution_height"]}'
                               if camera["resolution_width"] else None),
                "fps": camera["fps"],
            })
        return {
            "id": str(row["id"]), "name": row["name"], "active": row["active"],
            "room": {"id": str(row["room_id"]), "code": row["room_code"], "name": row["room_name"],
                     "active": row["room_active"]},
            "status": status, "last_seen_at": _iso(row["last_seen_at"]),
            "seconds_since_seen": round(seen_age) if seen_age is not None else None,
            "client": row["client"], "open_alerts": int(row["open_alerts"]), "cameras": camera_views,
        }

    def stations(self, principal, args) -> dict[str, Any]:
        unknown = sorted(set(args))
        if unknown:
            raise ApiError.invalid_request("Unknown query parameter(s)", details={"unknown_parameters": unknown})
        with self.database.connect() as connection:
            rows, cameras, now = self._rows(connection, principal.institution_id)
        items = [self._station_view(row, cameras, now) for row in rows]
        counts = {state: sum(1 for i in items if i["status"] == state)
                  for state in ("online", "offline", "never_seen", "disabled")}
        camera_counts = {state: sum(1 for i in items for c in i["cameras"] if c["status"] == state)
                         for state in ("ok", "unverified", "error", "unknown", "disabled")}
        return {"generated_at": _iso(now), "stale_seconds": self.stale_seconds, "camera_frames_seconds": self.frames_seconds,
                "totals": {"stations": counts, "cameras": camera_counts}, "items": items}

    def station(self, principal, raw_station_id: str) -> dict[str, Any]:
        station_id = _uuid(raw_station_id)
        with self.database.connect() as connection:
            rows, cameras, now = self._rows(connection, principal.institution_id, station_id)
            if not rows:
                raise ApiError.not_found("Station not found")
            events = connection.execute(text("""
                SELECT e.id, e.kind, e.camera_id, e.detail, e.occurred_at, e.resolved_at
                FROM station_event e WHERE e.station_id = :station ORDER BY e.occurred_at DESC LIMIT :limit
            """), {"station": station_id, "limit": EVENT_LIMIT}).mappings().all()
        view = self._station_view(rows[0], cameras, now)
        view["events"] = [self._event_view(e) for e in events]
        return view

    @staticmethod
    def _event_view(event, station_name=None) -> dict[str, Any]:
        detail = event["detail"] if isinstance(event["detail"], dict) else {}
        view = {"id": str(event["id"]), "kind": event["kind"],
                "camera_id": str(event["camera_id"]) if event["camera_id"] else None,
                "error": detail.get("error"), "occurred_at": _iso(event["occurred_at"]),
                "resolved_at": _iso(event["resolved_at"]),
                "open": event["kind"] in OPEN_ALERT_KINDS and event["resolved_at"] is None}
        if station_name is not None:
            view = {"station_id": str(event["station_id"]), "station_name": station_name, **view}
        return view

    def alerts(self, principal, args) -> dict[str, Any]:
        unknown = sorted(set(args) - {"status", "limit"})
        if unknown:
            raise ApiError.invalid_request("Unknown query parameter(s)", details={"unknown_parameters": unknown})
        status = args.get("status", "open")
        if status not in ("open", "all"):
            raise ApiError.invalid_request("status must be open or all", details={"field": "status"})
        try:
            limit = min(ALERT_LIMIT_MAX, max(1, int(args.get("limit", 50))))
        except ValueError:
            raise ApiError.invalid_request("limit must be an integer", details={"field": "limit"}) from None
        open_only = "AND e.resolved_at IS NULL AND e.kind IN ('heartbeat_lost', 'camera_error')" if status == "open" else ""
        with self.database.connect() as connection:
            rows = connection.execute(text(f"""
                SELECT e.id, e.station_id, s.name AS station_name, e.kind, e.camera_id, e.detail, e.occurred_at, e.resolved_at
                FROM station_event e JOIN capture_station s ON s.id = e.station_id
                WHERE e.institution_id = :inst {open_only}
                ORDER BY e.occurred_at DESC LIMIT :limit
            """), {"inst": principal.institution_id, "limit": limit}).mappings().all()
        return {"status": status, "items": [self._event_view(r, r["station_name"]) for r in rows]}

    # --- collector ----------------------------------------------------------------------------------------

    def observe_frames(self, activity: list[dict[str, Any]]) -> int:
        """Record frames VisionWorker received (server-side camera evidence) on the session's station."""
        updated = 0
        with self.database.begin() as connection:
            for item in activity:
                try:
                    session_id = uuid.UUID(str(item.get("session_id")))
                    seen = datetime.fromisoformat(str(item.get("last_frame_at")).replace("Z", "+00:00"))
                except (TypeError, ValueError):
                    continue
                session = connection.execute(text("""
                    SELECT w.station_id, w.camera_id, r.institution_id
                    FROM work_session w JOIN capture_station s ON s.id = w.station_id
                    JOIN operating_room r ON r.id = s.room_id WHERE w.id = :id
                """), {"id": session_id}).mappings().first()
                if session is None:
                    continue
                updated += connection.execute(text("""
                    INSERT INTO station_status (station_id, institution_id, frames_seen_at, frames_session_id,
                                                frames_camera_id, updated_at)
                    VALUES (:station, :inst, :seen, :session, :camera, now())
                    ON CONFLICT (station_id) DO UPDATE SET
                        frames_seen_at = GREATEST(station_status.frames_seen_at, EXCLUDED.frames_seen_at),
                        frames_session_id = EXCLUDED.frames_session_id, frames_camera_id = EXCLUDED.frames_camera_id,
                        updated_at = now()
                """), {"station": session["station_id"], "inst": session["institution_id"], "seen": seen,
                       "session": session_id, "camera": session["camera_id"]}).rowcount
        return updated

    def detect_lost(self) -> int:
        """Open heartbeat_lost for active stations that reported before and stopped (one open alert each)."""
        with self.database.begin() as connection:
            return connection.execute(text("""
                INSERT INTO station_event (institution_id, station_id, kind, detail, occurred_at)
                SELECT st.institution_id, st.station_id, 'heartbeat_lost',
                       jsonb_build_object('last_seen_at', to_char(st.last_seen_at AT TIME ZONE 'UTC',
                                                                  'YYYY-MM-DD"T"HH24:MI:SS"Z"')),
                       now()
                FROM station_status st
                JOIN capture_station s ON s.id = st.station_id AND s.active
                JOIN operating_room r ON r.id = s.room_id AND r.active
                WHERE st.last_seen_at IS NOT NULL
                  AND st.last_seen_at < now() - make_interval(secs => :stale)
                ON CONFLICT (station_id, kind) WHERE resolved_at IS NULL AND kind IN ('heartbeat_lost', 'camera_error')
                DO NOTHING
            """), {"stale": self.stale_seconds}).rowcount
