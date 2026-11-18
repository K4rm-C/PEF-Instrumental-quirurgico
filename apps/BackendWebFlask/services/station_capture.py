"""MS-05: the station and camera of an Operator's capture (portal side).

- The station is the session's own station (work_session.station_id, set when it was scheduled): the browser
  never says which station it is.
- The camera is chosen among the ACTIVE cameras of that station (capture_camera, migration 012). Choosing it
  sets work_session.camera_id and is audited (session.camera_selected); it changes nothing in the counting
  flow (no new blocking, references and validations untouched).
- The heartbeat of the capture page is relayed to MonitoringService (POST /api/v1/monitoring/stations/{id}/
  heartbeat) with the Operator's own Bearer token: MonitoringService checks role, institution and station
  again. A MonitoringService outage never affects the capture (the relay answers "not recorded").

work_session.camera_id is read and written with SQL (not mapped on the ORM model) so that a portal running
against a database without migration 012 keeps working: the camera features then report "unavailable".
"""
from __future__ import annotations

import logging
import os
import uuid
from typing import Any

import requests
from flask import g
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from extensions import db

logger = logging.getLogger("pef.station_capture")
CAMERA_STATES = ("ok", "error", "unavailable", "unknown")


class StationCaptureError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.message, self.status = message, status


def _monitoring_url() -> str:
    return os.getenv("MONITORING_SERVICE_URL", "").strip().rstrip("/")


def capture_context(session_id) -> dict[str, Any]:
    """Station, its active cameras and the selected camera of a session (empty lists without migration 012)."""
    try:
        with db.session.begin_nested():
            row = db.session.execute(text("""
                SELECT w.station_id, w.camera_id, s.name AS station_name, s.active AS station_active
                FROM work_session w LEFT JOIN capture_station s ON s.id = w.station_id WHERE w.id = :id
            """), {"id": session_id}).mappings().first()
            cameras = [] if row is None or row["station_id"] is None else db.session.execute(text("""
                SELECT id, name, device_type, resolution_width, resolution_height, fps
                FROM capture_camera WHERE station_id = :station AND active ORDER BY name
            """), {"station": row["station_id"]}).mappings().all()
    except SQLAlchemyError:
        logger.warning("camera features unavailable (migration 012 not applied?)")
        return {"available": False, "station_id": None, "station_name": None, "cameras": [], "camera_id": None}
    if row is None:
        return {"available": False, "station_id": None, "station_name": None, "cameras": [], "camera_id": None}
    return {
        "available": True,
        "station_id": str(row["station_id"]) if row["station_id"] else None,
        "station_name": row["station_name"],
        "camera_id": str(row["camera_id"]) if row["camera_id"] else None,
        "cameras": [{"id": str(c["id"]), "name": c["name"], "device_type": c["device_type"],
                     "resolution": f'{c["resolution_width"]}x{c["resolution_height"]}' if c["resolution_width"] else None,
                     "fps": c["fps"]} for c in cameras],
    }


def select_camera(*, session_id, operator_user_id, institution_id, camera_id, ip=None) -> dict[str, Any]:
    """Assigned Operator, session in progress, camera active and of the session's station (same institution)."""
    from services.rf_session import _audit, _lock_work_session
    try:
        camera_uuid = uuid.UUID(str(camera_id))
    except (TypeError, ValueError):
        raise StationCaptureError("Choose a camera of this station.", 400) from None
    _lock_work_session(session_id)
    session = db.session.execute(text("""
        SELECT w.user_id, w.station_id, w.camera_id, st.code AS status_code, o.institution_id
        FROM work_session w JOIN cat_session_status st ON st.id = w.status_id
        LEFT JOIN operation o ON o.id = w.operation_id WHERE w.id = :id
    """), {"id": session_id}).mappings().first()
    if session is None or (institution_id and session["institution_id"] != institution_id):
        raise StationCaptureError("Session not found.", 404)
    if session["user_id"] != operator_user_id:
        raise StationCaptureError("Session is not assigned to you.", 403)
    if session["status_code"] != "in_progress":
        raise StationCaptureError("The camera can only be chosen while the session is in progress.", 409)
    camera = db.session.execute(text("""
        SELECT c.id, c.name, c.active, c.station_id, c.institution_id FROM capture_camera c WHERE c.id = :id
    """), {"id": camera_uuid}).mappings().first()
    if (camera is None or camera["station_id"] != session["station_id"]
            or camera["institution_id"] != session["institution_id"]):
        raise StationCaptureError("Choose a camera of this station.", 400)
    if not camera["active"]:
        raise StationCaptureError("This camera is inactive.", 409)
    if session["camera_id"] == camera_uuid:
        db.session.rollback()
        return {"camera_id": str(camera_uuid), "camera_name": camera["name"], "changed": False}
    db.session.execute(text("UPDATE work_session SET camera_id = :camera, updated_at = now() WHERE id = :id"),
                       {"camera": camera_uuid, "id": session_id})
    _audit(user_id=operator_user_id, action="session.camera_selected", resource_id=session_id,
           institution_id=session["institution_id"], ip=ip)
    db.session.commit()
    return {"camera_id": str(camera_uuid), "camera_name": camera["name"], "changed": True}


def relay_heartbeat(*, station_id, camera_id, camera_status: str, camera_error: str | None) -> dict[str, Any]:
    """Forward the capture page's heartbeat to MonitoringService. Never raises (best effort)."""
    base, token = _monitoring_url(), getattr(g, "auth_access_token", None)
    if not base or not token or not station_id:
        return {"recorded": False, "reason": "monitoring_disabled" if not base else "no_station"}
    body = {"camera_status": camera_status if camera_status in CAMERA_STATES else "unknown",
            "camera_id": camera_id, "client": "portal-capture"}
    if body["camera_status"] in ("error", "unavailable") and camera_error:
        body["camera_error"] = "".join(ch for ch in str(camera_error) if ch.isalnum() or ch in "_.:-")[:120] or None
    try:
        response = requests.post(f"{base}/api/v1/monitoring/stations/{station_id}/heartbeat", json=body, timeout=3,
                                 headers={"Authorization": f"Bearer {token}", "Accept": "application/json"})
    except requests.RequestException as exc:
        return {"recorded": False, "reason": f"monitoring_unavailable ({type(exc).__name__})"}
    if response.ok:
        data = response.json()
        return {"recorded": True, "station_status": data.get("status"),
                "camera_status": next((c["status"] for c in data.get("cameras", []) if c["id"] == camera_id), None)}
    try:
        reason = (response.json() or {}).get("details", {}).get("reason") or (response.json() or {}).get("error")
    except ValueError:
        reason = None
    return {"recorded": False, "reason": reason or f"HTTP {response.status_code}"}
