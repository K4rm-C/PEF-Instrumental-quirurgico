"""ELECTRON-01: read-only observer of a session's live inference over the existing ``/ws`` endpoint.

The Operator's capture (portal browser camera over ``/ws`` or an uploaded video) is the single owner of the
frames. A desktop monitor that wants to *see* that work opens the same WebSocket and sends, as its first
message::

    {"type": "monitor.subscribe", "session_id": "<uuid>", "auth_token": "<m1 ticket from SessionService>"}

From then on the connection is observe-only:
- it never calls bind / prepare_live_run / cancel and never receives frames from the client, so the Operator's
  run, its hold/tracking state and every persisted count are untouched;
- the worker pushes ``monitor.state`` (with the latest annotated frame only when a *new* inference exists) and,
  while nothing changes, a light ``monitor.state`` without frame every ``MONITOR_HEARTBEAT_S``;
- the ticket is checked on every cycle: on expiry without ``monitor.renew`` the worker sends
  ``error/session_expired`` and closes; a renew for another session is ``unauthorized`` and closes;
- at most ``MONITOR_MAX_OBSERVERS`` observers per session.

Frames are sent at most every ``MONITOR_MIN_INTERVAL_S`` (default 0.4 s ≈ 2.5 fps): a monitor needs to be
readable from a distance, not to mirror camera FPS.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from datetime import datetime, timezone
from typing import Any, Callable

from monitor_ticket import monitor_ticket_expiry

logger = logging.getLogger("pef.vision.monitor")

MONITOR_SECRET = (os.getenv("VISION_MONITOR_SECRET") or "").strip()


def _float_env(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except ValueError:
        return default


MONITOR_MIN_INTERVAL_S = max(0.1, _float_env("VISION_MONITOR_MIN_INTERVAL_S", 0.4))
MONITOR_HEARTBEAT_S = max(0.5, _float_env("VISION_MONITOR_HEARTBEAT_S", 2.0))
MONITOR_MAX_OBSERVERS = max(1, int(_float_env("VISION_MONITOR_MAX_OBSERVERS", 3)))

_observers: dict[str, int] = {}
_observers_lock = threading.Lock()


def monitor_enabled(secret: str | None = None) -> bool:
    return bool(MONITOR_SECRET if secret is None else secret)


def _iso(epoch: float | None) -> str | None:
    if epoch is None:
        return None
    return datetime.fromtimestamp(float(epoch), tz=timezone.utc).isoformat()


def _acquire(session_id: str) -> bool:
    with _observers_lock:
        if _observers.get(session_id, 0) >= MONITOR_MAX_OBSERVERS:
            return False
        _observers[session_id] = _observers.get(session_id, 0) + 1
        return True


def _release(session_id: str) -> None:
    with _observers_lock:
        left = _observers.get(session_id, 0) - 1
        if left > 0:
            _observers[session_id] = left
        else:
            _observers.pop(session_id, None)


def build_monitor_state(state: Any, session_id: str, *, include_frame: bool, now: float | None = None) -> dict[str, Any]:
    """Compact, read-only projection of a SessionState (None = worker has no binding for the session)."""
    now = time.time() if now is None else now
    if state is None:
        return {
            "type": "monitor.state",
            "session_id": session_id,
            "sent_at": _iso(now),
            "worker": {"status": "unbound", "capture_source": None, "frames_processed": 0, "run_id": None,
                       "last_frame_at": None, "frame_age_s": None, "message": "Session not bound on this worker"},
            "frame": None,
            "counts_by_family": [],
            "detections": [],
        }
    last_frame_at = getattr(state, "last_frame_at", None)
    counts = []
    for code, meta in (getattr(state, "counts", None) or {}).items():
        if not isinstance(meta, dict):
            continue
        counts.append({
            "family_code": meta.get("family_code") or code,
            "family_id": meta.get("family_id"),
            "detected_quantity": int(meta.get("detected_quantity") or 0),
        })
    detections = []
    for box in getattr(state, "boxes", None) or []:
        xy = box.get("xyxy") or [0, 0, 0, 0]
        try:
            x1, y1, x2, y2 = [float(v) for v in xy[:4]]
        except (TypeError, ValueError):
            continue
        detections.append({
            "family_code": box.get("family_code") or "",
            "family_name": box.get("family_name") or box.get("label") or "",
            "confidence": float(box.get("confidence") or 0.0),
            "high_confidence": bool(box.get("high_confidence")),
            "held": bool(box.get("held")),
            "bbox": [x1, y1, max(0.0, x2 - x1), max(0.0, y2 - y1)],
        })
    frames_processed = int(getattr(state, "frames_processed", 0) or 0)
    jpeg = getattr(state, "frame_jpeg_b64", None)
    return {
        "type": "monitor.state",
        "session_id": session_id,
        "sent_at": _iso(now),
        "worker": {
            "status": getattr(state, "status", None),
            "capture_source": getattr(state, "capture_source", None),
            "capture_phase": getattr(state, "capture_phase", None),
            "message": getattr(state, "message", "") or "",
            "run_id": getattr(state, "run_id", None),
            "recording_run_id": (getattr(state, "run_id", None) if getattr(state, "recording_name", None)
                                 and getattr(state, "status", None) == "ready" else None),
            "frames_processed": frames_processed,
            "model_version": getattr(state, "model_version", None),
            "last_frame_at": _iso(last_frame_at),
            "frame_age_s": None if last_frame_at is None else round(max(0.0, now - float(last_frame_at)), 3),
            "progress": getattr(state, "progress", None),
        },
        "frame": ({"jpeg_b64": jpeg, "frames_processed": frames_processed, "run_id": getattr(state, "run_id", None)}
                  if include_frame and jpeg else None),
        "counts_by_family": counts,
        "detections": detections,
    }


def _send(ws, payload: dict[str, Any]) -> bool:
    try:
        ws.send(json.dumps(payload))
        return True
    except Exception:  # noqa: BLE001 - peer gone
        return False


def _error(ws, code: str, message: str) -> None:
    _send(ws, {"type": "error", "code": code, "message": message})


def run_observer(ws, first: dict[str, Any], *, get_state: Callable[[str], Any], secret: str | None = None,
                 clock: Callable[[], float] = time.time) -> None:
    """Serve one observe-only connection until it closes, expires or is refused."""
    secret = MONITOR_SECRET if secret is None else secret
    session_id = str(first.get("session_id") or "")
    if not monitor_enabled(secret):
        _error(ws, "monitor_disabled", "Monitor mode is not configured on this worker")
        return
    expires_at = monitor_ticket_expiry(str(first.get("auth_token") or ""), session_id=session_id, secret=secret,
                                       now=clock())
    if expires_at is None:
        _error(ws, "unauthorized", "Invalid or expired monitor ticket")
        return
    if not _acquire(session_id):
        _error(ws, "busy", "Too many monitors for this session")
        return
    logger.info("monitor.subscribe session=%s", session_id)
    last_key: tuple[Any, ...] | None = None
    last_frame_sent = 0.0
    last_sent = 0.0
    try:
        _send(ws, {"type": "monitor.subscribed", "session_id": session_id, "expires_at": expires_at,
                   "min_interval_s": MONITOR_MIN_INTERVAL_S, "heartbeat_s": MONITOR_HEARTBEAT_S})
        while True:
            try:
                raw = ws.receive(timeout=MONITOR_MIN_INTERVAL_S)
            except Exception:  # noqa: BLE001 - closed by the peer
                break
            if raw is not None:
                try:
                    msg = json.loads(raw)
                except (TypeError, ValueError):
                    msg = None
                mtype = msg.get("type") if isinstance(msg, dict) else None
                if mtype == "monitor.renew":
                    renewed = monitor_ticket_expiry(str(msg.get("auth_token") or ""), session_id=session_id,
                                                    secret=secret, now=clock())
                    if renewed is None:
                        _error(ws, "unauthorized", "Renewal refused")
                        break
                    expires_at = renewed
                    _send(ws, {"type": "monitor.renewed", "session_id": session_id, "expires_at": expires_at})
                elif mtype == "monitor.unsubscribe":
                    break
                else:
                    # Observers cannot bind, submit frames or cancel: anything else is refused, not executed.
                    _error(ws, "forbidden", "Observe-only connection")
            now = clock()
            if now > expires_at:
                _error(ws, "session_expired", "Monitor ticket expired")
                break
            state = get_state(session_id)
            key = (None,) if state is None else (
                getattr(state, "run_id", None), getattr(state, "frames_processed", 0), getattr(state, "status", None),
                getattr(state, "capture_source", None),
            )
            changed = key != last_key
            frame_due = changed and (now - last_frame_sent) >= MONITOR_MIN_INTERVAL_S
            if frame_due or (now - last_sent) >= MONITOR_HEARTBEAT_S:
                payload = build_monitor_state(state, session_id, include_frame=frame_due, now=now)
                if not _send(ws, payload):
                    break
                last_sent = now
                if frame_due:
                    last_key, last_frame_sent = key, now
    finally:
        _release(session_id)
        logger.info("monitor.closed session=%s", session_id)
