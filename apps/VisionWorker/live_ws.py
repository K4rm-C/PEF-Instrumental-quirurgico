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
from ws_ticket import ticket_claims

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
        "model_sha256": state.model_sha256,   # MS-02: weights actually used
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
        "model_sha256": state.model_sha256,   # MS-02: weights actually used
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


class LiveAuth:
    """Authorization of one open live connection (MS-03): user, ticket expiry, revocation flag."""

    def __init__(self, session_id: str, user_id: str, exp: int):
        self.session_id, self.user_id, self.exp = session_id, user_id, exp
        self.revoked = threading.Event()

    def problem(self) -> tuple[str, str] | None:
        if self.revoked.is_set():
            return "revoked", "Authorization was revoked; sign in again to continue"
        if time.time() > self.exp:
            return "session_expired", "Authorization expired; the live preview was stopped"
        return None


_live_lock = threading.Lock()
_live_by_user: dict[str, set] = {}


def _register(auth: LiveAuth) -> None:
    with _live_lock:
        _live_by_user.setdefault(auth.user_id, set()).add(auth)


def _unregister(auth) -> None:
    if auth is None:
        return
    with _live_lock:
        connections = _live_by_user.get(auth.user_id)
        if connections is not None:
            connections.discard(auth)
            if not connections:
                _live_by_user.pop(auth.user_id, None)


def revoke_user(user_id: str) -> int:
    """Mark every open live connection of a user as revoked; each closes within about a second."""
    with _live_lock:
        connections = list(_live_by_user.get(str(user_id), ()))
    for auth in connections:
        auth.revoked.set()
    return len(connections)


# MS-05: server-side evidence that a station's camera works — the last time a valid camera frame of each
# session arrived over an authorized live connection (decoded JPEG; independent of the inference result).
_activity_lock = threading.Lock()
_frame_activity: dict[str, dict[str, Any]] = {}
ACTIVITY_KEEP_SECONDS = 3600


def record_frame(session_id: str) -> None:
    now = time.time()
    with _activity_lock:
        entry = _frame_activity.setdefault(str(session_id), {"frames": 0})
        entry["frames"] += 1
        entry["last_frame_at"] = now
        for key in [k for k, v in _frame_activity.items() if now - v["last_frame_at"] > ACTIVITY_KEEP_SECONDS]:
            _frame_activity.pop(key, None)


def frame_activity() -> list[dict[str, Any]]:
    from datetime import datetime, timezone
    with _activity_lock:
        items = list(_frame_activity.items())
    return [{"session_id": session_id, "frames": entry["frames"],
             "last_frame_at": datetime.fromtimestamp(entry["last_frame_at"], timezone.utc).isoformat().replace("+00:00", "Z")}
            for session_id, entry in items]


def live_connections() -> int:
    with _live_lock:
        return sum(len(items) for items in _live_by_user.values())


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
        auth: LiveAuth | None = None
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
                    if auth is None or auth.problem() is not None:
                        return          # never infer or send results to a connection that lost authorization
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
                if auth is not None:
                    reason = auth.problem()
                    if reason is not None:
                        # MS-03: server-side cut. Stop inference, tell the client why, close the socket.
                        stop.set()
                        if bound is not None:
                            cancel_capture(bound)
                        _error(ws, code=reason[0], message=reason[1])
                        break
                try:
                    raw = ws.receive(timeout=1.0)
                except Exception:
                    break
                if raw is None:
                    if getattr(ws, "connected", False):
                        continue        # 1 s tick: re-check authorization even when the client is silent
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
                    claims = ticket_claims(token, session_id=sid, secret=WS_SECRET) if sid else None
                    if claims is None:
                        _error(ws, code="unauthorized", message="Ticket inválido o expirado")
                        return
                    persistent_state = get_state(sid)
                    if persistent_state is None:
                        _error(ws, code="unauthorized", message="Session not bound")
                        return
                    if persistent_state.status in {"loading", "processing"}:
                        _error(ws, code="busy", message="File job running; retry later")
                        return
                    # Live preview has its own ephemeral SessionState. Never mutate the
                    # persisted V1 file/video state or its registered first/last counts.
                    state = SessionState(session_id=sid)
                    state.expected = dict(persistent_state.expected)
                    state.class_map = dict(persistent_state.class_map)
                    state.model_version = persistent_state.model_version
                    # MS-02: the live run infers with the session's pinned weights, never a client choice.
                    state.model_id = persistent_state.model_id
                    state.model_sha256 = persistent_state.model_sha256
                    state.model_source = persistent_state.model_source
                    state.conf_threshold = persistent_state.conf_threshold
                    try:
                        prepare_live_run(state)
                    except Exception as exc:
                        _error(ws, code="model_unavailable", message=str(exc))
                        return
                    _unregister(auth)
                    auth = LiveAuth(sid, claims["user_id"], claims["exp"])
                    _register(auth)
                    bound = state
                    send(_status_payload(bound, "ready"))
                    continue

                if mtype == "session.renew":
                    # MS-03: a fresh ticket (issued by the portal only to a still-valid JWT) extends the connection.
                    claims = (ticket_claims(str(msg.get("auth_token") or ""), session_id=auth.session_id,
                                            secret=WS_SECRET) if auth is not None else None)
                    if claims is None or claims["user_id"] != auth.user_id:
                        _error(ws, code="unauthorized", message="Ticket inválido o expirado")
                        break
                    auth.exp = claims["exp"]
                    send({"type": "auth.renewed", "session_id": auth.session_id, "expires_at": auth.exp})
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
                record_frame(bound.session_id)
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
            _unregister(auth)
            try:
                ws.close()
            except Exception:  # noqa: BLE001 - already closed by the client
                pass
