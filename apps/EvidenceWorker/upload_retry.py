"""Redis retry queue for evidence clips that failed Garage/PG/Mongo publish."""

from __future__ import annotations

import json
import logging
import time
from typing import Any

from config import (
    REDIS_URL,
    UPLOAD_RETRY_LIST_KEY,
    UPLOAD_RETRY_MAX_ATTEMPTS,
)

logger = logging.getLogger(__name__)
_client = None


def _redis():
    global _client
    if _client is not None:
        return _client if _client is not False else None
    try:
        import redis
        client = redis.Redis.from_url(REDIS_URL, decode_responses=True)
        client.ping()
        _client = client
    except Exception as exc:  # noqa: BLE001
        logger.warning("upload retry redis unavailable: %s", exc)
        _client = False  # type: ignore[assignment]
        return None
    return _client


def enqueue_upload_retry(meta: dict[str, Any], *, error: str | None = None) -> bool:
    """Push clip meta for later Garage retry. Never raises."""
    client = _redis()
    if client is None:
        return False
    payload = {
        "clip_id": meta.get("clip_id"),
        "session_id": meta.get("session_id"),
        "local_path": meta.get("local_path") or meta.get("path"),
        "reason": meta.get("reason"),
        "phase_id": meta.get("phase_id"),
        "phase_code": meta.get("phase_code"),
        "phase_name": meta.get("phase_name"),
        "ratio_label": meta.get("ratio_label"),
        "detected_total": meta.get("detected_total"),
        "expected_total": meta.get("expected_total"),
        "occurred_at": meta.get("occurred_at"),
        "frame_count": meta.get("frame_count"),
        "size_bytes": meta.get("size_bytes"),
        "kind": meta.get("kind"),
        "encoder": meta.get("encoder"),
        "attempts": int(meta.get("upload_attempts") or 0),
        "last_error": (error or "")[:400],
        "enqueued_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "next_attempt_at": time.time(),
    }
    try:
        client.rpush(UPLOAD_RETRY_LIST_KEY, json.dumps(payload, separators=(",", ":")))
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("enqueue upload retry failed: %s", exc)
        return False


def pop_due_retries(limit: int = 5) -> list[dict[str, Any]]:
    """Pop up to `limit` due items (next_attempt_at <= now). Re-queues not-due items."""
    client = _redis()
    if client is None:
        return []
    due: list[dict[str, Any]] = []
    deferred: list[str] = []
    now = time.time()
    scanned = 0
    try:
        while scanned < 40 and len(due) < limit:
            raw = client.lpop(UPLOAD_RETRY_LIST_KEY)
            if raw is None:
                break
            scanned += 1
            try:
                item = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if not isinstance(item, dict):
                continue
            next_at = float(item.get("next_attempt_at") or 0)
            attempts = int(item.get("attempts") or 0)
            if attempts >= UPLOAD_RETRY_MAX_ATTEMPTS:
                logger.warning(
                    "upload retry exhausted clip=%s session=%s err=%s",
                    item.get("clip_id"), item.get("session_id"), item.get("last_error"),
                )
                continue
            if next_at > now:
                deferred.append(json.dumps(item, separators=(",", ":")))
                continue
            due.append(item)
        for raw in deferred:
            client.rpush(UPLOAD_RETRY_LIST_KEY, raw)
    except Exception as exc:  # noqa: BLE001
        logger.debug("pop_due_retries: %s", exc)
    return due


def requeue_with_backoff(item: dict[str, Any], *, error: str | None = None) -> bool:
    attempts = int(item.get("attempts") or 0) + 1
    # 30s, 60s, 120s, … capped at 15 min
    delay = min(900.0, 30.0 * (2 ** max(0, attempts - 1)))
    item["attempts"] = attempts
    item["last_error"] = (error or item.get("last_error") or "")[:400]
    item["next_attempt_at"] = time.time() + delay
    client = _redis()
    if client is None:
        return False
    try:
        client.rpush(UPLOAD_RETRY_LIST_KEY, json.dumps(item, separators=(",", ":")))
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("requeue upload retry failed: %s", exc)
        return False
