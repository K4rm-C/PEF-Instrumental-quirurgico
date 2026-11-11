"""HMAC ticket for browser → VisionWorker WebSocket (JWT cookie stays on Flask origin).

MS-03: <session_id>.<user_id>.<exp>.<nonce>.<hmac>. The worker keeps a live connection only while a valid
ticket of the same session and user covers it; the Operator page renews it every ttl/3 through this portal
endpoint, which requires a JWT that AuthService still accepts. A revoked user therefore loses the live preview
at the latest when the ticket expires (VISION_WS_TICKET_TTL, default 120 s), or at once when the portal revokes
(vision_bridge.revoke_live_user)."""

from __future__ import annotations

import hashlib
import hmac
import os
import time
import uuid


WS_SECRET = (os.getenv("VISION_WS_SECRET") or "pef-demo-vision-ws").strip()
WS_TICKET_TTL_S = max(30, int(os.getenv("VISION_WS_TICKET_TTL", "120")))
VISION_WORKER_WS_URL = os.getenv("VISION_WORKER_WS_URL", "ws://127.0.0.1:5002/ws")


def issue_ws_ticket(session_id: str, user_id: str, *, ttl_s: int | None = None) -> dict:
    ttl = int(ttl_s or WS_TICKET_TTL_S)
    exp = int(time.time()) + ttl
    nonce = uuid.uuid4().hex
    sid, uid = str(session_id), str(user_id)
    msg = f"{sid}:{uid}:{exp}:{nonce}".encode("utf-8")
    sig = hmac.new(WS_SECRET.encode("utf-8"), msg, hashlib.sha256).hexdigest()
    return {
        "ticket": f"{sid}.{uid}.{exp}.{nonce}.{sig}",
        "renew_after_s": max(10, ttl // 3),
        "ws_url": VISION_WORKER_WS_URL,
        "expires_at": exp,
        "ttl_s": ttl,
    }
