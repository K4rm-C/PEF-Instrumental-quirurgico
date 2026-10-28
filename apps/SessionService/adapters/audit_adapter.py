"""Transitional audit writer (decision D5; PEF-DS07 `access_audit`, PEF-DS09 §1.3.4 rule 5).

Until an Audit Service exists, Session Service writes `access_audit` directly — and ONLY through
this module. The row is inserted on the caller's connection, i.e. inside the same PostgreSQL
transaction as the domain change it describes: if the audit insert fails, the whole change rolls
back (no domain mutation without its audit record).

Stored: occurred_at, actor (user), action, resource (work_session id), institution, outcome,
correlation_id. Never stored: tokens, request bodies, Redis data, IP or other personal data.
`access_audit` is audit; `count_event` remains the Session business fact.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy.engine import Connection

from repositories.tables import access_audit

# Action names shared with the legacy portal (rf_session._audit) for semantic continuity.
ACTION_SESSION_SCHEDULED = "session.scheduled"
ACTION_INVENTORY_UPDATED = "session.inventory_updated"
ACTION_SESSION_START = "session.start"
ACTION_MANUAL_CLOSE = "session.manual_close"   # POST count-report
ACTION_DISCREPANCY_RESOLVE = "discrepancy.resolve"   # resource: the discrepancy's work_session
ACTION_SPD_CLOSE = "session.spd_close"
ACTION_OPERATOR_CLOSE = "session.operator_close"   # reconciled vision session closed by its Operator
ACTION_INSTRUMENTS_ADDED = "session.instruments_added"   # confirmed additions while in progress
ACTION_SCHEDULED_UPDATED = "session.scheduled_updated"
ACTION_PRIVACY_AGREEMENT = "privacy.session_agreement"

ACTOR_TYPE = "user"
RESOURCE_TYPE = "work_session"
OUTCOME = "success"
DENIED = "denied"
NIL_UUID = uuid.UUID(int=0)
# correlation_id is stored exactly as answered to the client: DS09 v0.1.5 caps X-Correlation-ID at 64
# characters (= access_audit.correlation_id VARCHAR(64)) and longer headers are replaced, never truncated.


def record(
    connection: Connection,
    *,
    action: str,
    actor_user_id: uuid.UUID,
    session_id: uuid.UUID,
    institution_id: uuid.UUID,
    correlation_id: str | None,
    occurred_at: datetime,
) -> None:
    connection.execute(
        access_audit.insert().values(
            id=uuid.uuid4(),
            occurred_at=occurred_at,
            actor_type=ACTOR_TYPE,
            actor_user_id=actor_user_id,
            action=action,
            resource_type=RESOURCE_TYPE,
            resource_id=session_id,
            institution_id=institution_id,
            outcome=OUTCOME,
            correlation_id=correlation_id,
        )
    )


def record_denied(database, *, actor_user_id: uuid.UUID, institution_id: uuid.UUID, method: str,
                  session_id: uuid.UUID | None, correlation_id: str | None, occurred_at: datetime) -> None:
    """A request refused by the role check (403): DENIED_<METHOD>_WORK_SESSION in its own transaction.

    Never raises: failing to write the audit row must not turn a 403 into a 500.
    """
    try:
        with database.begin() as connection:
            connection.execute(
                access_audit.insert().values(
                    id=uuid.uuid4(), occurred_at=occurred_at, actor_type=ACTOR_TYPE, actor_user_id=actor_user_id,
                    action=f"DENIED_{method.upper()}_WORK_SESSION"[:64], resource_type=RESOURCE_TYPE,
                    resource_id=session_id or NIL_UUID, institution_id=institution_id, outcome=DENIED,
                    correlation_id=correlation_id,
                )
            )
    except Exception:  # noqa: BLE001
        import logging
        logging.getLogger("session.audit").warning("could not record denied %s", method)
