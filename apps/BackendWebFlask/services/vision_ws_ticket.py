"""HMAC ticket for browser → VisionWorker WebSocket (JWT cookie stays on Flask origin)."""

from __future__ import annotations

import hashlib
import hmac
import os
import time
import uuid


WS_SECRET = (os.getenv("VISION_WS_SECRET") or "pef-demo-vision-ws").strip()
WS_TICKET_TTL_S = max(60, int(os.getenv("VISION_WS_TICKET_TTL", "300")))
VISION_WORKER_WS_URL = os.getenv("VISION_WORKER_WS_URL", "ws://127.0.0.1:5002/ws")


def issue_ws_ticket(session_id: str, *, ttl_s: int | None = None) -> dict:
    ttl = int(ttl_s or WS_TICKET_TTL_S)
    exp = int(time.time()) + ttl
    nonce = uuid.uuid4().hex
    sid = str(session_id)
    msg = f"{sid}:{exp}:{nonce}".encode("utf-8")
    sig = hmac.new(WS_SECRET.encode("utf-8"), msg, hashlib.sha256).hexdigest()
    return {
        "ticket": f"{sid}.{exp}.{nonce}.{sig}",
        "ws_url": VISION_WORKER_WS_URL,
        "expires_at": exp,
        "ttl_s": ttl,
    }
