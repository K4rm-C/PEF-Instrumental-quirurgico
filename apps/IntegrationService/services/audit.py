"""access_audit rows written by the Integration Service (PEF-DS07): actor user OR machine client.

Stored: time, actor, action, resource, institution, outcome, correlation id, IP. Never: tokens, secrets,
request bodies, FHIR payloads.
"""

from __future__ import annotations

import ipaddress
import uuid

from flask import g, has_request_context, request
from sqlalchemy import text

from auth import NIL_UUID, Principal

_INSERT = text("""
INSERT INTO access_audit (id, occurred_at, actor_type, actor_user_id, actor_client_id, action, resource_type,
                          resource_id, institution_id, outcome, correlation_id, ip)
VALUES (:id, now(), :actor_type, :actor_user_id, :actor_client_id, :action, :resource_type, :resource_id,
        :institution_id, :outcome, :correlation_id, CAST(:ip AS inet))
""")


def _ip() -> str | None:
    if not has_request_context():
        return None
    try:
        return str(ipaddress.ip_address((request.remote_addr or "").strip()))
    except ValueError:
        return None


def record(connection, principal: Principal, action: str, resource_type: str, resource_id=None, *,
           outcome: str = "success", correlation_id: str | None = None) -> None:
    corr = correlation_id or (getattr(g, "correlation_id", None) if has_request_context() else None) or uuid.uuid4().hex
    connection.execute(_INSERT, {
        "id": uuid.uuid4(), **principal.actor, "action": action[:64], "resource_type": resource_type[:64],
        "resource_id": resource_id or NIL_UUID, "institution_id": principal.institution_id, "outcome": outcome,
        "correlation_id": corr[:64], "ip": _ip()})


def record_as(connection, *, actor_user_id, institution_id, action: str, resource_type: str, resource_id,
              outcome: str = "success") -> None:
    """Background worker rows: attributed to the user who requested the work."""
    connection.execute(_INSERT, {
        "id": uuid.uuid4(), "actor_type": "user", "actor_user_id": actor_user_id, "actor_client_id": None,
        "action": action[:64], "resource_type": resource_type[:64], "resource_id": resource_id or NIL_UUID,
        "institution_id": institution_id, "outcome": outcome, "correlation_id": uuid.uuid4().hex, "ip": None})
