"""MS-02: the portal signs every bind (HMAC-SHA256 of the raw body with VISION_WS_SECRET, header
X-PEF-Signature: sha256=<hex>). Only the portal, which resolves the session's pinned model server side, can tell
the worker which weights a session uses."""
from __future__ import annotations

import hashlib
import hmac
import os

from config import WS_SECRET

ALLOW_UNPINNED_BIND = (os.getenv("VISION_ALLOW_UNPINNED_BIND") or "").strip().lower() in {"1", "true", "yes"}


def sign(body: bytes, secret: str = WS_SECRET) -> str:
    return "sha256=" + hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()


def verify_bind_signature(body: bytes, header: str | None, secret: str = WS_SECRET) -> bool:
    if not secret or not header:
        return False
    return hmac.compare_digest(sign(body, secret), header.strip())
