"""HMAC ticket: Flask issues, VisionWorker verifies. JWT HttpOnly never reaches :5002."""

from __future__ import annotations

import hmac
import hashlib
import time


def verify_ticket(ticket: str, *, session_id: str, secret: str) -> bool:
    if not ticket or not secret or not session_id:
        return False
    parts = str(ticket).split(".")
    if len(parts) != 4:
        return False
    sid, exp_s, nonce, sig = parts
    if sid != str(session_id):
        return False
    try:
        exp = int(exp_s)
    except (TypeError, ValueError):
        return False
    if exp < int(time.time()):
        return False
    if not nonce:
        return False
    msg = f"{sid}:{exp}:{nonce}".encode("utf-8")
    expected = hmac.new(secret.encode("utf-8"), msg, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, sig)
