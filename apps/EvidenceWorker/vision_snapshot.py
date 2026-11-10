"""Fetch live counts from VisionWorker HTTP for hourly clip ratio (approach 2C)."""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
from typing import Any

from config import VISION_WORKER_TIMEOUT, VISION_WORKER_URL

logger = logging.getLogger(__name__)


def ratio_from_counts(counts: dict[str, Any] | None) -> dict[str, Any]:
    expected_total = 0
    detected_total = 0
    for meta in (counts or {}).values():
        if not isinstance(meta, dict):
            continue
        expected_total += int(meta.get("expected_quantity") or 0)
        detected_total += int(meta.get("detected_quantity") or 0)
    ratio_label = f"{detected_total}/{expected_total}" if expected_total else None
    return {
        "ratio_label": ratio_label,
        "detected_total": detected_total,
        "expected_total": expected_total,
    }


def fetch_session_ratio(session_id: str) -> dict[str, Any] | None:
    """GET /sessions/<id>/state → ratio fields. Soft-fail: returns None."""
    sid = str(session_id or "").strip()
    if not sid:
        return None
    base = VISION_WORKER_URL.rstrip("/")
    url = f"{base}/sessions/{sid}/state"
    try:
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=VISION_WORKER_TIMEOUT) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
        payload = json.loads(raw)
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, json.JSONDecodeError, OSError) as exc:
        logger.debug("vision snapshot unavailable session=%s: %s", sid, exc)
        return None
    if not isinstance(payload, dict) or not payload.get("ok"):
        return None
    session = payload.get("session") or {}
    if not isinstance(session, dict):
        return None
    out = ratio_from_counts(session.get("counts") or {})
    out["source"] = "vision_worker_http"
    return out
