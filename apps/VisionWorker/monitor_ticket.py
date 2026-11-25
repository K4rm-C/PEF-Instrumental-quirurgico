"""ELECTRON-01: observe-only ticket for the desktop station monitor.

Issued by SessionService (services/station_monitor.py) to an authenticated ``station_operator`` for a vision
session in progress at their station; verified here. Format::

    m1.<session_id>.<exp>.<nonce>.<hex hmac-sha256("pef-monitor:v1:<session_id>:<exp>:<nonce>")>

Separate secret (``VISION_MONITOR_SECRET``) and separate MAC domain from the portal's frame-submit tickets
(ws_ticket.py): a monitor ticket can only *watch* a session, never bind it, submit frames or cancel it, and a
portal ticket is never accepted as a monitor ticket. Empty secret = monitor mode disabled.
"""

from __future__ import annotations

import hashlib
import hmac
import time

PREFIX = "m1"
DOMAIN = "pef-monitor:v1"


def monitor_ticket_expiry(ticket: str, *, session_id: str, secret: str, now: float | None = None) -> int | None:
    """Expiry (epoch seconds) of a valid ticket for ``session_id``; None if invalid, foreign or expired."""
    if not ticket or not secret or not session_id:
        return None
    parts = str(ticket).split(".")
    if len(parts) != 5:
        return None
    prefix, sid, exp_s, nonce, sig = parts
    if prefix != PREFIX or sid != str(session_id) or not nonce:
        return None
    try:
        exp = int(exp_s)
    except (TypeError, ValueError):
        return None
    if exp < int(now if now is not None else time.time()):
        return None
    msg = f"{DOMAIN}:{sid}:{exp}:{nonce}".encode("utf-8")
    expected = hmac.new(secret.encode("utf-8"), msg, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, sig):
        return None
    return exp
