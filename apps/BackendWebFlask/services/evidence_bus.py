"""Publish evidence triggers for the EvidenceWorker (Redis list; never blocks OP).

Message contract (JSON):
  session_id, reason, phase_id, occurred_at

Reasons: start | phase | close | hourly
Vision-only callers should skip manual_no_privacy sessions.
Publish failures are logged and swallowed so Start / phase / close never roll back.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

logger = logging.getLogger(__name__)

TRIGGER_LIST_KEY = os.getenv("EVIDENCE_TRIGGER_LIST", "pef:evidence:triggers")
REDIS_URL = os.getenv("REDIS_URL", "redis://:MedRedis@localhost:6379/0")

_client = None


def _redis():
    global _client
    if _client is not None:
        return _client
    try:
        import redis
    except ImportError:
        logger.warning("redis package missing; evidence triggers disabled")
        return None
    try:
        _client = redis.Redis.from_url(REDIS_URL, decode_responses=True)
        _client.ping()
    except Exception as exc:  # noqa: BLE001
        logger.warning("evidence bus redis unavailable: %s", exc)
        _client = None
    return _client


def _iso(value: datetime | str | None) -> str:
    if value is None:
        return datetime.now(timezone.utc).isoformat()
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.isoformat()
    return str(value)


def publish_evidence_trigger(
    *,
    session_id: UUID | str,
    reason: str,
    phase_id: UUID | str | None = None,
    occurred_at: datetime | str | None = None,
    extra: dict[str, Any] | None = None,
) -> bool:
    """RPUSH trigger. Returns True if enqueued. Never raises to callers."""
    reason_norm = (reason or "").strip().lower()
    if reason_norm not in {"start", "phase", "close", "hourly"}:
        logger.warning("ignored evidence trigger reason=%s", reason)
        return False
    payload = {
        "session_id": str(session_id),
        "reason": reason_norm,
        "phase_id": str(phase_id) if phase_id else None,
        "occurred_at": _iso(occurred_at),
    }
    if extra:
        payload["extra"] = extra
    client = _redis()
    if client is None:
        return False
    try:
        client.rpush(TRIGGER_LIST_KEY, json.dumps(payload, separators=(",", ":")))
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("evidence trigger publish failed: %s", exc)
        return False
