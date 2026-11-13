"""Transitional audit writer (PEF-DS07 `access_audit`) for the Catalog management endpoints.

Until an Audit Service exists, Catalog writes `access_audit` directly and ONLY through this module,
inside the same transaction as the change it records. Action names are the ones the portal's
Administration always used (CREATE_INSTRUMENT_FAMILY, UPDATE_KIT, SUPERSEDE_KIT, DENIED_...), so the
Administrator and Supervisor audit logs read Catalog changes exactly like before.

Stored: occurred_at, actor (user), action, resource type/id, institution, outcome, correlation_id and
the caller's IP (the portal server for portal requests). Never stored: tokens, bodies or secrets.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone

from flask import g, request
from sqlalchemy.engine import Connection
from sqlalchemy.exc import SQLAlchemyError

from repositories.tables import access_audit

logger = logging.getLogger("catalog.audit")
NIL_UUID = uuid.UUID(int=0)


def _client_ip() -> str | None:
    try:
        return request.remote_addr or None
    except RuntimeError:
        return None


def record(connection: Connection, *, action: str, resource_type: str, resource_id: uuid.UUID | None,
           principal, outcome: str = "success") -> None:
    connection.execute(
        access_audit.insert().values(
            id=uuid.uuid4(),
            occurred_at=datetime.now(timezone.utc),
            actor_type="user",
            actor_user_id=principal.user_id,
            action=action[:64],
            resource_type=resource_type[:64],
            resource_id=resource_id or NIL_UUID,
            institution_id=principal.institution_id,
            outcome=outcome,
            correlation_id=(getattr(g, "correlation_id", None) or None),
            ip=_client_ip(),
        )
    )


def record_update(connection: Connection, *, entity: str, resource_type: str, resource_id: uuid.UUID,
                  principal, was_active: bool | None = None, is_active: bool | None = None,
                  changed: bool = True) -> None:
    """UPDATE_<ENTITY> when fields changed, plus ACTIVATE_/DEACTIVATE_ on a status flip."""
    if changed:
        record(connection, action=f"UPDATE_{entity}", resource_type=resource_type, resource_id=resource_id,
               principal=principal)
    if was_active is not None and is_active is not None and was_active != is_active:
        record(connection, action=f"{'ACTIVATE' if is_active else 'DEACTIVATE'}_{entity}",
               resource_type=resource_type, resource_id=resource_id, principal=principal)


def record_denied(database, principal, resource_type: str, resource_id: str | None) -> None:
    """A role without permission tried a management operation: own transaction, never raises."""
    try:
        target = uuid.UUID(str(resource_id)) if resource_id else NIL_UUID
    except ValueError:
        target = NIL_UUID
    try:
        with database.begin() as connection:
            record(connection, action=f"DENIED_{request.method}_{resource_type}".upper(),
                   resource_type=resource_type, resource_id=target, principal=principal, outcome="denied")
    except SQLAlchemyError:
        logger.error("denied attempt could not be audited (%s)", resource_type)
