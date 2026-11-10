"""Non-blocking JPEG fan-out to EvidenceWorker (drop-oldest). Never awaits encode/S3.

Live path: enqueue in a tiny RAM slot and return immediately so the WS thread
never waits on Redis JSON/LPUSH (avoids staggered camera vs overlay).
"""

from __future__ import annotations

import json
import logging
import threading
import time
from typing import Any

from config import (
    EVIDENCE_FRAME_LIST_PREFIX,
    EVIDENCE_FRAME_MAX,
    EVIDENCE_QUEUE_ENABLED,
    REDIS_URL,
)

logger = logging.getLogger(__name__)
_client = None
_client_lock = threading.Lock()

# Single latest-wins slot per session + one background flusher (never blocks YOLO/WS).
_pending: dict[str, tuple[str, float]] = {}
_pending_lock = threading.Lock()
_wake = threading.Event()
_worker_started = False
_worker_lock = threading.Lock()
_import_failed = False


def _redis():
    global _client, _import_failed
    if not EVIDENCE_QUEUE_ENABLED or _import_failed:
        return None
    if _client is not None:
        return _client if _client is not False else None
    with _client_lock:
        if _client is not None:
            return _client if _client is not False else None
        try:
            import redis
        except ImportError:
            logger.warning("redis missing; evidence frame queue off")
            _import_failed = True
            return None
        try:
            client = redis.Redis.from_url(REDIS_URL, decode_responses=True)
            client.ping()
            _client = client
        except Exception as exc:  # noqa: BLE001
            logger.warning("evidence frame redis unavailable: %s", exc)
            _client = False  # type: ignore[assignment]
            return None
    return _client if _client is not False else None


def _ensure_worker() -> None:
    global _worker_started
    if _worker_started or not EVIDENCE_QUEUE_ENABLED:
        return
    with _worker_lock:
        if _worker_started:
            return
        t = threading.Thread(target=_flush_loop, name="evidence-frame-flush", daemon=True)
        t.start()
        _worker_started = True


def _flush_one(session_id: str, jpeg_b64: str, captured_at: float) -> None:
    client = _redis()
    if client is None:
        return
    key = f"{EVIDENCE_FRAME_LIST_PREFIX}{session_id}"
    payload: dict[str, Any] = {
        "session_id": session_id,
        "ts": float(captured_at),
        "jpeg_b64": jpeg_b64,
        "content_type": "image/jpeg",
    }
    try:
        pipe = client.pipeline(transaction=False)
        pipe.lpush(key, json.dumps(payload, separators=(",", ":")))
        pipe.ltrim(key, 0, max(0, int(EVIDENCE_FRAME_MAX) - 1))
        pipe.expire(key, 3600)
        pipe.execute()
    except Exception as exc:  # noqa: BLE001
        logger.debug("evidence frame push skipped: %s", exc)


def _flush_loop() -> None:
    while True:
        _wake.wait(timeout=0.05)
        _wake.clear()
        batch: dict[str, tuple[str, float]] = {}
        with _pending_lock:
            if not _pending:
                continue
            batch = dict(_pending)
            _pending.clear()
        for sid, (jpeg_b64, ts) in batch.items():
            _flush_one(sid, jpeg_b64, ts)


def push_camera_jpeg(
    session_id: str,
    jpeg_b64: str,
    *,
    captured_at: float | None = None,
) -> bool:
    """Enqueue latest JPEG for session. Returns immediately; never blocks YOLO/WS."""
    if not session_id or not jpeg_b64 or not EVIDENCE_QUEUE_ENABLED:
        return False
    if _import_failed:
        return False
    _ensure_worker()
    ts = float(captured_at if captured_at is not None else time.time())
    with _pending_lock:
        # Drop-oldest / latest-wins: only the newest frame per session is kept.
        _pending[session_id] = (jpeg_b64, ts)
    _wake.set()
    return True
