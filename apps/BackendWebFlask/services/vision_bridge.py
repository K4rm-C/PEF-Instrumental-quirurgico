"""HTTP bridge from BackendWebFlask to the VisionWorker process."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
from typing import Any
from uuid import UUID

import requests
from sqlalchemy import select, text

from extensions import db
from models.CatInstrumentCategory import CatInstrumentCategory
from models.ExpectedInventory import ExpectedInventory
from models.InstrumentFamily import InstrumentFamily
from models.MediaAsset import MediaAsset
from models.ModelClass import ModelClass
from models.YoloModel import YoloModel

VISION_WORKER_URL = os.getenv("VISION_WORKER_URL", "http://127.0.0.1:5002").rstrip("/")
VISION_WORKER_TIMEOUT = float(os.getenv("VISION_WORKER_TIMEOUT", "30"))
CONF_DISPLAY_THRESHOLD = float(os.getenv("VISION_CONF_DISPLAY_THRESHOLD", "0.70"))
# MS-02: binds are signed with the same secret as the WS tickets; loading a model can take longer than a call.
VISION_WS_SECRET = (os.getenv("VISION_WS_SECRET") or "pef-demo-vision-ws").strip()
MODEL_BIND_TIMEOUT = float(os.getenv("VISION_MODEL_BIND_TIMEOUT", "180"))
MODEL_URL_TTL = int(os.getenv("VISION_MODEL_URL_TTL", "300"))


class VisionBridgeError(Exception):
    def __init__(self, message: str, status_code: int = 502):
        super().__init__(message)
        self.message = message
        self.status_code = status_code


def worker_health() -> dict[str, Any]:
    try:
        resp = requests.get(f"{VISION_WORKER_URL}/health", timeout=min(VISION_WORKER_TIMEOUT, 5))
        if resp.status_code >= 400:
            return {"ok": False, "error": f"Worker HTTP {resp.status_code}"}
        return resp.json()
    except requests.RequestException as exc:
        return {"ok": False, "error": str(exc)}


def active_model_payload() -> dict[str, Any]:
    """Payload of the model active NOW (Administration view; binds use the session's pinned model, MS-02)."""
    model = db.session.scalar(select(YoloModel).where(YoloModel.active.is_(True)))
    return model_payload(model)


def model_payload(model: YoloModel | None) -> dict[str, Any]:
    if model is None:
        return {
            "model_version": "yolo26l-demo",
            "model_id": None,
            "conf_threshold": CONF_DISPLAY_THRESHOLD,
            "class_map": [],
        }
    rows = db.session.execute(
        select(ModelClass, InstrumentFamily, CatInstrumentCategory)
        .join(InstrumentFamily, InstrumentFamily.id == ModelClass.family_id)
        .join(CatInstrumentCategory, CatInstrumentCategory.id == InstrumentFamily.category_id)
        .where(ModelClass.model_id == model.id)
    ).all()
    class_map = [
        {
            "yolo_class_id": mc.yolo_class_id,
            "family_id": str(family.id),
            "family_code": family.code,
            "family_name": family.name,
            "category_name": category.name,
        }
        for mc, family, category in rows
    ]
    return {
        "model_version": model.version_tag,
        "model_id": str(model.id),
        "conf_threshold": CONF_DISPLAY_THRESHOLD,
        "class_map": class_map,
    }


def expected_payload_for_session(session_id: UUID) -> list[dict[str, Any]]:
    rows = db.session.execute(
        select(ExpectedInventory, InstrumentFamily, CatInstrumentCategory)
        .join(InstrumentFamily, InstrumentFamily.id == ExpectedInventory.family_id)
        .join(CatInstrumentCategory, CatInstrumentCategory.id == InstrumentFamily.category_id)
        .where(ExpectedInventory.session_id == session_id)
        .order_by(InstrumentFamily.name)
    ).all()
    return [
        {
            "family_id": str(family.id),
            "family_code": family.code,
            "family_name": family.name,
            "category_name": category.name,
            "expected_quantity": inv.expected_quantity,
        }
        for inv, family, category in rows
    ]


def _weights_reference(model: YoloModel) -> dict[str, Any]:
    """Where the worker finds the pinned weights: Administration storage reference (+ a short-lived presigned
    link for S3). Never a filesystem path; the worker validates the key and verifies the SHA-256."""
    asset = db.session.get(MediaAsset, model.media_asset_id) if model.media_asset_id else None
    if asset is None:
        return {}
    reference = {"bucket": asset.bucket, "key": asset.object_key}
    if asset.bucket != "local":
        from services.admin_local import storage
        try:
            reference["url"] = storage.presigned_get(asset.bucket, asset.object_key, MODEL_URL_TTL)
        except storage.StorageUnavailable as exc:
            raise VisionBridgeError(f"Model storage unavailable: {exc}", 503) from exc
    return reference


def session_model_payload(session_id: UUID) -> dict[str, Any]:
    """MS-02 / P1+P2: the model PINNED to the session (pins it now at the first inference use if needed)."""
    from services.model_pinning import ModelPinError, model_for_session
    try:
        model = model_for_session(session_id)
    except ModelPinError as exc:
        raise VisionBridgeError(exc.message, exc.status_code) from exc
    payload = model_payload(model)
    names = db.session.execute(text("SELECT class_names FROM yolo_model WHERE id = :i"), {"i": model.id}).scalar()
    payload["model"] = {
        "model_id": str(model.id),
        "model_version": model.version_tag,
        "sha256": (model.checksum or "").strip().lower(),
        "storage": _weights_reference(model),
        "class_names": names if isinstance(names, dict) else None,
    }
    return payload


def _signed_post(url: str, body: dict[str, Any], timeout: float):
    raw = json.dumps(body, separators=(",", ":"), sort_keys=True).encode("utf-8")
    signature = "sha256=" + hmac.new(VISION_WS_SECRET.encode("utf-8"), raw, hashlib.sha256).hexdigest()
    return requests.post(url, data=raw, timeout=timeout,
                         headers={"Content-Type": "application/json", "X-PEF-Signature": signature})


def camera_payload_for_session(session_id: UUID) -> dict[str, Any] | None:
    """MS-11 (R08): configuration of the camera chosen for the session (station camera, Administration):
    resolution, FPS and ROI travel to VisionWorker in the signed bind. None until a camera is chosen."""
    row = db.session.execute(text("""
        SELECT c.id, c.name, c.device_type, c.resolution_width, c.resolution_height, c.fps, c.roi
        FROM work_session w JOIN capture_camera c ON c.id = w.camera_id AND c.station_id = w.station_id
        WHERE w.id = :s
    """), {"s": session_id}).mappings().first()
    if row is None:
        return None
    return {"id": str(row["id"]), "name": row["name"], "device_type": row["device_type"],
            "resolution_width": row["resolution_width"], "resolution_height": row["resolution_height"],
            "fps": row["fps"], "roi": row["roi"] if isinstance(row["roi"], dict) else None,
            # MS-11: a source registered as 'simulated' is a test source; its captures never validate hardware.
            "simulated": row["device_type"] == "simulated",
            # MS-12: technician-measured calibration (informational; never used to count).
            "calibration": _camera_calibration(row)}


def _camera_calibration(camera) -> dict[str, Any] | None:
    cal = db.session.execute(text("""
        SELECT id, mm_per_pixel, frame_width, frame_height, performed_at FROM capture_camera_calibration
        WHERE camera_id = :c ORDER BY performed_at DESC, id DESC LIMIT 1
    """), {"c": camera["id"]}).mappings().first()
    if cal is None or (camera["resolution_width"], camera["resolution_height"]) != (cal["frame_width"], cal["frame_height"]):
        return None
    return {"id": str(cal["id"]), "mm_per_pixel": float(cal["mm_per_pixel"]), "frame_width": cal["frame_width"],
            "frame_height": cal["frame_height"], "performed_at": cal["performed_at"].isoformat()}


def bind_session(session_id: UUID) -> dict[str, Any]:
    model = session_model_payload(session_id)
    body = {
        "camera": camera_payload_for_session(session_id),
        "expected": expected_payload_for_session(session_id),
        "class_map": model["class_map"],
        "model_version": model["model_version"],
        "conf_threshold": model["conf_threshold"],
        "model": model["model"],
    }
    try:
        resp = _signed_post(f"{VISION_WORKER_URL}/sessions/{session_id}/bind", body,
                            max(VISION_WORKER_TIMEOUT, MODEL_BIND_TIMEOUT))
    except requests.RequestException as exc:
        raise VisionBridgeError(f"Vision worker unreachable: {exc}", 503) from exc
    if resp.status_code >= 400:
        raise _worker_error(resp, "Vision bind failed")
    data = resp.json()
    loaded = ((data or {}).get("model") or {}).get("sha256")
    if loaded != model["model"]["sha256"]:
        raise VisionBridgeError("The vision worker did not load this session's model weights.", 409)
    return data


def revoke_live_user(user_id) -> int | None:
    """MS-03: close every open live WebSocket of a user in the worker (signed). Best effort: returns the number
    of connections closed, or None if the worker could not be reached (the ticket expiry still applies)."""
    try:
        response = _signed_post(f"{VISION_WORKER_URL}/auth/revoke", {"user_id": str(user_id)},
                                min(VISION_WORKER_TIMEOUT, 3))
    except requests.RequestException:
        return None
    if response.status_code != 200:
        return None
    return (response.json() or {}).get("revoked_connections")


def assert_session_model(session_id: UUID, worker_session: dict[str, Any]) -> None:
    """Counts are only registered from inference made with the session's pinned weights (MS-02)."""
    pinned = db.session.execute(text(
        "SELECT m.checksum FROM work_session w JOIN yolo_model m ON m.id = w.yolo_model_id WHERE w.id = :s"
    ), {"s": session_id}).scalar()
    used = (worker_session or {}).get("model_sha256")
    if pinned and used and used != pinned.strip().lower():
        raise VisionBridgeError("The vision worker is not using this session's model weights.", 409)
    if pinned and not used:
        raise VisionBridgeError("The vision worker did not report the model weights it used.", 409)


def unbind_session(session_id: UUID) -> dict[str, Any]:
    try:
        resp = requests.post(
            f"{VISION_WORKER_URL}/sessions/{session_id}/unbind",
            timeout=min(VISION_WORKER_TIMEOUT, 8),
        )
        if resp.status_code >= 400:
            return {"ok": False, "error": resp.text}
        return resp.json()
    except requests.RequestException as exc:
        return {"ok": False, "error": str(exc)}


def fetch_state(session_id: UUID) -> dict[str, Any]:
    try:
        resp = requests.get(
            f"{VISION_WORKER_URL}/sessions/{session_id}/state",
            timeout=min(VISION_WORKER_TIMEOUT, 8),
        )
    except requests.RequestException as exc:
        raise VisionBridgeError(f"Vision worker unreachable: {exc}", 503) from exc
    if resp.status_code == 404:
        # Auto-rebind if worker restarted but session still in progress.
        bind_session(session_id)
        resp = requests.get(
            f"{VISION_WORKER_URL}/sessions/{session_id}/state",
            timeout=min(VISION_WORKER_TIMEOUT, 8),
        )
    if resp.status_code >= 400:
        raise VisionBridgeError(f"Vision state failed: {resp.text}", resp.status_code)
    data = resp.json()
    assert_session_model(session_id, (data or {}).get("session") or {})
    return data


def fetch_state_quick(session_id: UUID, timeout: float = 1.5) -> dict[str, Any] | None:
    """Current worker run (with a processed frame) for a checkpoint, or None; never raises."""
    try:
        resp = requests.get(f"{VISION_WORKER_URL}/sessions/{session_id}/state", timeout=timeout)
        state = (resp.json() or {}).get("session") or {} if resp.status_code == 200 else {}
    except (requests.RequestException, ValueError):
        return None
    if state.get("status") not in {"ready", "processing"} or not state.get("frames_processed"):
        return None
    try:
        assert_session_model(session_id, state)   # evidence only from the session's pinned weights (MS-02)
    except VisionBridgeError:
        return None
    return state


def fetch_timeline(session_id: UUID, run_id: str | None = None) -> dict[str, Any]:
    """Sampled tallies written by the worker during video processing."""
    params = {}
    if run_id:
        params["run_id"] = run_id
    try:
        resp = requests.get(
            f"{VISION_WORKER_URL}/sessions/{session_id}/timeline",
            params=params,
            timeout=min(VISION_WORKER_TIMEOUT, 15),
        )
    except requests.RequestException as exc:
        raise VisionBridgeError(f"Vision worker unreachable: {exc}", 503) from exc
    if resp.status_code >= 400:
        raise VisionBridgeError(f"Vision timeline failed: {resp.text}", resp.status_code)
    return resp.json()


TRACKING_PAGE = 500
TRACKING_MAX_PAGES = 40


def fetch_tracking_events(session_id: UUID, run_id: str | None) -> list[dict[str, Any]] | None:
    """MS-12 (ms-tracking-instrumental): the COMPLETE tracking event log of run `run_id` (paginated), or None when the
    worker cannot give it (unreachable, another run, malformed). Never raises: traceability never blocks a count."""
    if not run_id:
        return None
    events: list[dict[str, Any]] = []
    for page in range(TRACKING_MAX_PAGES):
        try:
            resp = requests.get(f"{VISION_WORKER_URL}/sessions/{session_id}/tracking",
                                params={"offset": page * TRACKING_PAGE, "limit": TRACKING_PAGE},
                                timeout=min(VISION_WORKER_TIMEOUT, 8))
            data = resp.json() if resp.status_code == 200 else None
        except (requests.RequestException, ValueError):
            return None
        if not isinstance(data, dict) or data.get("run_id") != run_id or not isinstance(data.get("events"), list):
            return None
        events.extend(e for e in data["events"] if isinstance(e, dict))
        if len(events) >= int(data.get("events_total") or 0) or not data["events"]:
            return events
    return events


def _worker_error(resp, default: str) -> VisionBridgeError:
    detail = None
    if resp.headers.get("content-type", "").startswith("application/json"):
        detail = (resp.json() or {}).get("error")
    return VisionBridgeError(detail or resp.text or default, resp.status_code)


def store_video(session_id: UUID, file_storage) -> dict[str, Any]:
    """Single-video flow: store the video; the worker analyses only its first valid frame."""
    try:
        files = {"video": (file_storage.filename, file_storage.stream,
                           file_storage.mimetype or "application/octet-stream")}
        resp = requests.post(f"{VISION_WORKER_URL}/sessions/{session_id}/video", files=files,
                             timeout=max(VISION_WORKER_TIMEOUT, 120))
    except requests.RequestException as exc:
        raise VisionBridgeError(f"Vision upload failed: {exc}", 503) from exc
    if resp.status_code >= 400:
        raise _worker_error(resp, "Vision upload failed")
    return resp.json()


def process_stored_video(session_id: UUID, video_id: str) -> dict[str, Any]:
    """Single-video flow: full run of the stored video (after the initial count)."""
    try:
        resp = requests.post(f"{VISION_WORKER_URL}/sessions/{session_id}/video/{video_id}/process",
                             timeout=min(VISION_WORKER_TIMEOUT, 15))
    except requests.RequestException as exc:
        raise VisionBridgeError(f"Vision worker unreachable: {exc}", 503) from exc
    if resp.status_code >= 400:
        raise _worker_error(resp, "Vision processing could not start")
    return resp.json()


def discard_video(session_id: UUID) -> None:
    """Drop the uploaded video (best effort)."""
    try:
        requests.delete(f"{VISION_WORKER_URL}/sessions/{session_id}/video", timeout=min(VISION_WORKER_TIMEOUT, 8))
    except requests.RequestException:
        pass


def upload_video(session_id: UUID, file_storage) -> dict[str, Any]:
    try:
        files = {
            "video": (
                file_storage.filename,
                file_storage.stream,
                file_storage.mimetype or "application/octet-stream",
            )
        }
        resp = requests.post(
            f"{VISION_WORKER_URL}/sessions/{session_id}/process-video",
            files=files,
            timeout=max(VISION_WORKER_TIMEOUT, 120),
        )
    except requests.RequestException as exc:
        raise VisionBridgeError(f"Vision upload failed: {exc}", 503) from exc
    if resp.status_code >= 400:
        detail = resp.json().get("error") if resp.headers.get("content-type", "").startswith("application/json") else resp.text
        raise VisionBridgeError(detail or "Vision upload failed", resp.status_code)
    return resp.json()
