"""Live camera WebSocket (DS10).

Two buffers that must not be mixed:

- Slot YOLO (this module): at most one pending JPEG. While run_frame is busy the
  latest frame overwrites the slot; there is no YOLO backlog. Camera 30 fps always
  outruns inference on this hardware.
- Ring GIF (future, not implemented): JPEG of the *camera* at camera fps, ~5–8 s
  RAM window, assembled off the hot path into a DS05 gif + DS06 checkpoint.
  Mongo/Garage/GIF encoding are out of this delivery.
"""

from __future__ import annotations

import json
import threading
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Callable

from flask import request

from config import WS_ORIGINS, WS_SECRET
from inference import (
    SessionState,
    cancel_capture,
    decode_jpeg_b64,
    prepare_live_run,
    run_frame,
)
from ws_ticket import verify_ticket

GetState = Callable[[str], SessionState | None]


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _error(ws, *, code: str, message: str, frame_id: str | None = None) -> None:
    ws.send(json.dumps({
        "type": "error",
        "code": code,
        "message": message,
        "frame_id": frame_id,
    }))


def _status_payload(state: SessionState, slot_state: str) -> dict[str, Any]:
    return {
        "type": "worker.status",
        "session_id": state.session_id,
        "state": slot_state,
        "model_version": state.model_version,
        "message": state.message,
    }


def _detections_result(
    state: SessionState,
    *,
    frame_id: str,
    correlation_id: str | None,
) -> dict[str, Any]:
    counts = []
    for code, meta in (state.counts or {}).items():
        if not isinstance(meta, dict):
            continue
        counts.append({
            "family_code": meta.get("family_code") or code,
            "detected_quantity": int(meta.get("detected_quantity") or 0),
        })
    detections = []
    for box in state.boxes or []:
        xy = box.get("xyxy") or [0, 0, 0, 0]
        try:
            x1, y1, x2, y2 = [float(v) for v in xy[:4]]
        except (TypeError, ValueError):
            continue
        detections.append({
            "family_code": box.get("family_code") or "",
            "confidence": float(box.get("confidence") or 0),
            "bbox": [x1, y1, max(0.0, x2 - x1), max(0.0, y2 - y1)],
        })
    return {
        "type": "detections.result",
        "session_id": state.session_id,
        "frame_id": frame_id,
        "model_version": state.model_version,
        "inferred_at": _now_iso(),
        "latency_ms": state.latency_ms,
        "last_yolo_ms": state.last_yolo_ms,
        "last_post_ms": state.last_post_ms,
        "bbox_space": "pixels",
        "detections": detections,
        "counts_by_family": counts,
        "correlation_id": correlation_id,
        "session": state.to_public(),
    }


def _origin_ok() -> bool:
    origin = (request.headers.get("Origin") or "").strip()
    if not origin:
        return True
    return origin in WS_ORIGINS


def register_live_ws(sock, *, get_state: GetState) -> None:
    @sock.route("/ws")
    def live_ws(ws):  # noqa: ANN001
        if not _origin_ok():
            _error(ws, code="unauthorized", message="Origin not allowed")
            return

        bound: SessionState | None = None
        slot: dict[str, Any] | None = None
        slot_lock = threading.Lock()
        send_lock = threading.Lock()
        stop = threading.Event()
        wake = threading.Event()

        def send(payload: dict[str, Any]) -> None:
            with send_lock:
                try:
                    ws.send(json.dumps(payload))
                except Exception:
                    stop.set()

        def infer_loop() -> None:
            nonlocal slot
            while not stop.is_set():
                wake.wait(timeout=0.25)
                wake.clear()
                while not stop.is_set():
                    state = bound
                    if state is None or state.capture_source != "live" or state.cancel:
                        return
                    with slot_lock:
                        job = slot
                        slot = None
                    if job is None:
                        break
                    send(_status_payload(state, "busy"))
                    try:
                        state.frame_index = int(state.frames_processed or 0)
                        run_frame(job["frame"], state, video_t=time.time())
                        send(_detections_result(
                            state,
                            frame_id=job["frame_id"],
                            correlation_id=job.get("correlation_id"),
                        ))
                    except Exception as exc:  # noqa: BLE001
                        send({
                            "type": "error",
                            "code": "model_unavailable",
                            "message": str(exc),
                            "frame_id": job.get("frame_id"),
                        })
                    if state.capture_source == "live" and not state.cancel:
                        send(_status_payload(state, "ready"))

        worker = threading.Thread(target=infer_loop, daemon=True, name="yolo-live-slot")
        worker.start()

        try:
            while not stop.is_set():
                try:
                    raw = ws.receive()
                except Exception:
                    break
                if raw is None:
                    break
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    _error(ws, code="invalid_frame", message="JSON inválido")
                    continue
                if not isinstance(msg, dict):
                    _error(ws, code="invalid_frame", message="Mensaje no es objeto")
                    continue
                mtype = msg.get("type")

                if mtype == "session.bind":
                    sid = str(msg.get("session_id") or "")
                    token = str(msg.get("auth_token") or "")
                    if not sid or not verify_ticket(token, session_id=sid, secret=WS_SECRET):
                        _error(ws, code="unauthorized", message="Ticket inválido o expirado")
                        return
                    state = get_state(sid)
                    if state is None:
                        _error(ws, code="unauthorized", message="Session not bound")
                        return
                    if state.status == "processing" and state.capture_source == "file":
                        _error(ws, code="busy", message="File job running; cancel first")
                        return
                    prepare_live_run(state)
                    bound = state
                    send(_status_payload(bound, "ready"))
                    continue

                if bound is None:
                    _error(ws, code="unauthorized", message="Bind first")
                    continue

                if mtype == "session.cancel":
                    cancel_capture(bound)
                    send(_status_payload(bound, "stopping"))
                    break

                if mtype != "frame.submit":
                    _error(ws, code="invalid_frame", message=f"Unknown type {mtype}")
                    continue

                if bound.cancel or bound.capture_source != "live":
                    _error(ws, code="busy", message="Live not active")
                    continue

                frame_id = str(msg.get("frame_id") or uuid.uuid4())
                frame = decode_jpeg_b64(str(msg.get("image_base64") or ""))
                if frame is None:
                    _error(ws, code="invalid_frame", message="JPEG vacío o corrupto", frame_id=frame_id)
                    continue
                with slot_lock:
                    slot = {
                        "frame": frame,
                        "frame_id": frame_id,
                        "correlation_id": msg.get("correlation_id"),
                    }
                wake.set()
        finally:
            stop.set()
            wake.set()
            worker.join(timeout=2.0)
