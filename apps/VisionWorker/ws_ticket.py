"""HMAC ticket: Flask issues, VisionWorker verifies. JWT HttpOnly never reaches :5002.

MS-03: the ticket names the user and expires: <session_id>.<user_id>.<exp>.<nonce>.<hmac>. The live connection
stays authorized only while a valid ticket of the SAME session and user covers it (session.renew), and the portal
can revoke a user's open connections at once (POST /auth/revoke, signed). The portal issues tickets only to a
request whose JWT AuthService still accepts, so a revoked user cannot renew.
"""

from __future__ import annotations

import hmac
import hashlib
import time


def ticket_claims(ticket: str, *, session_id: str, secret: str, now: float | None = None) -> dict | None:
    """{'session_id', 'user_id', 'exp'} of a valid, unexpired ticket of that session; None otherwise."""
    if not ticket or not secret or not session_id:
        return None
    parts = str(ticket).split(".")
    if len(parts) != 5:
        return None
    sid, uid, exp_s, nonce, sig = parts
    if sid != str(session_id) or not uid or not nonce:
        return None
    try:
        exp = int(exp_s)
    except (TypeError, ValueError):
        return None
    if exp < int(now if now is not None else time.time()):
        return None
    msg = f"{sid}:{uid}:{exp}:{nonce}".encode("utf-8")
    expected = hmac.new(secret.encode("utf-8"), msg, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, sig):
        return None
    return {"session_id": sid, "user_id": uid, "exp": exp}


def verify_ticket(ticket: str, *, session_id: str, secret: str) -> bool:
    return ticket_claims(ticket, session_id=session_id, secret=secret) is not None
