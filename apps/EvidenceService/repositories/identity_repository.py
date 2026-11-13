"""PostgreSQL reads for the authentication context (PEF-DS01). Read-only."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import and_, collate, select
from sqlalchemy.engine import Connection

from repositories.tables import institution, role, user, user_role


def get_user_auth_state(connection: Connection, user_id: uuid.UUID) -> dict[str, Any] | None:
    row = connection.execute(
        select(user.c.id, user.c.active, user.c.institution_id, institution.c.active.label("institution_active"))
        .select_from(user.join(institution, institution.c.id == user.c.institution_id))
        .where(user.c.id == user_id)
    ).mappings().first()
    return dict(row) if row else None


def list_role_codes(connection: Connection, user_id: uuid.UUID, institution_id: uuid.UUID) -> list[str]:
    """Effective role codes: user_role rows whose role belongs to the user's institution."""
    rows = connection.execute(
        select(role.c.code)
        .select_from(user_role.join(role, role.c.id == user_role.c.role_id))
        .where(and_(user_role.c.user_id == user_id, role.c.institution_id == institution_id))
        .order_by(collate(role.c.code, "C"))
    ).scalars().all()
    return list(rows)
