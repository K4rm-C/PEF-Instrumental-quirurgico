"""PostgreSQL read access for Session Service (Phase A/B: no writes).

Every function receives an open Connection. Tenant scoping is part of the WHERE clause:
tenant(session) = operation.institution_id, so sessions without an operation never match.
String ordering uses COLLATE "C" (byte order) so results are identical in every environment.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import and_, case, collate, exists, func, select
from sqlalchemy.engine import Connection

from repositories.tables import (
    cat_discrepancy_reason,
    cat_event_type,
    cat_operation_phase,
    cat_session_status,
    count_event,
    discrepancy,
    expected_inventory,
    human_correction,
    institution,
    instrument_family,
    operation,
    role,
    session_processing_agreement,
    user,
    user_role,
    work_session,
)


def _c(column):
    return collate(column, "C")


# --- Identity (used by auth) ------------------------------------------------------------


def get_user_auth_state(connection: Connection, user_id: uuid.UUID) -> dict[str, Any] | None:
    row = connection.execute(
        select(
            user.c.id,
            user.c.active,
            user.c.institution_id,
            institution.c.active.label("institution_active"),
        )
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
        .order_by(_c(role.c.code))
    ).scalars().all()
    return list(rows)


# --- Sessions ---------------------------------------------------------------------------


def _open_discrepancies():
    return (
        select(func.count(discrepancy.c.id))
        .where(and_(discrepancy.c.session_id == work_session.c.id, discrepancy.c.resolved.is_(False)))
        .scalar_subquery()
    )


def _scoped_sessions(columns, institution_id: uuid.UUID, operator_user_id: uuid.UUID | None):
    """work_session joined to its operation inside the tenant; optionally only one operator's."""
    query = (
        select(*columns)
        .select_from(
            work_session.join(operation, operation.c.id == work_session.c.operation_id).join(
                cat_session_status, cat_session_status.c.id == work_session.c.status_id
            )
        )
        .where(operation.c.institution_id == institution_id)
    )
    if operator_user_id is not None:
        query = query.where(work_session.c.user_id == operator_user_id)
    return query


def list_sessions(
    connection: Connection, institution_id: uuid.UUID, operator_user_id: uuid.UUID | None
) -> list[dict[str, Any]]:
    columns = (
        work_session.c.id,
        cat_session_status.c.code.label("status_code"),
        work_session.c.capture_mode,
        operation.c.scheduled_at,
        work_session.c.started_at,
        work_session.c.ended_at,
        work_session.c.user_id,
        work_session.c.kit_id,
        _open_discrepancies().label("open_discrepancies"),
    )
    rows = connection.execute(
        _scoped_sessions(columns, institution_id, operator_user_id).order_by(
            operation.c.scheduled_at.desc().nulls_last(), work_session.c.id
        )
    ).mappings().all()
    return [dict(row) for row in rows]


def get_session(
    connection: Connection,
    session_id: uuid.UUID,
    institution_id: uuid.UUID,
    operator_user_id: uuid.UUID | None,
) -> dict[str, Any] | None:
    has_agreement = exists().where(session_processing_agreement.c.session_id == work_session.c.id)
    columns = (
        work_session.c.id,
        work_session.c.operation_id,
        work_session.c.user_id,
        work_session.c.station_id,
        work_session.c.kit_id,
        cat_session_status.c.code.label("status_code"),
        work_session.c.capture_mode,
        cat_operation_phase.c.code.label("current_phase_code"),
        operation.c.scheduled_at,
        work_session.c.started_at,
        work_session.c.ended_at,
        work_session.c.closed_by_user_id,
        _open_discrepancies().label("open_discrepancies"),
        has_agreement.label("privacy_agreement"),
    )
    query = _scoped_sessions(columns, institution_id, operator_user_id)
    query = query.outerjoin(
        cat_operation_phase, cat_operation_phase.c.id == work_session.c.current_phase_id
    ).where(work_session.c.id == session_id)
    row = connection.execute(query).mappings().first()
    return dict(row) if row else None


# --- Sub-resources (callers must have authorized the session first) -----------------------


def list_expected_inventory(connection: Connection, session_id: uuid.UUID) -> list[dict[str, Any]]:
    rows = connection.execute(
        select(
            expected_inventory.c.family_id,
            instrument_family.c.code.label("family_code"),
            expected_inventory.c.expected_quantity,
            expected_inventory.c.source,
        )
        .select_from(
            expected_inventory.join(instrument_family, instrument_family.c.id == expected_inventory.c.family_id)
        )
        .where(expected_inventory.c.session_id == session_id)
        .order_by(_c(instrument_family.c.code), expected_inventory.c.family_id)
    ).mappings().all()
    return [dict(row) for row in rows]


# Tie-break for events sharing occurred_at (DS09 GET /counts): the session opening first, the facts
# of the session next, the closing events last. Unrelated types share the "facts" rank (then id).
EVENT_ORDER_RANK = case(
    (cat_event_type.c.code == "session_open", 0),
    (cat_event_type.c.code == "manual_close", 2),
    (cat_event_type.c.code == "session_close", 3),
    else_=1,
)


def list_count_events(connection: Connection, session_id: uuid.UUID) -> list[dict[str, Any]]:
    rows = connection.execute(
        select(
            count_event.c.id,
            cat_event_type.c.code.label("event_type_code"),
            count_event.c.occurred_at,
            count_event.c.user_id,
            count_event.c.client_event_id,
            count_event.c.family_id,
            instrument_family.c.code.label("family_code"),
            count_event.c.expected_quantity,
            count_event.c.detected_quantity,
            count_event.c.payload,
            human_correction.c.justification.label("hc_justification"),
            human_correction.c.user_id.label("hc_user_id"),
            human_correction.c.recorded_at.label("hc_recorded_at"),
            human_correction.c.id.label("hc_id"),
        )
        .select_from(
            count_event.join(cat_event_type, cat_event_type.c.id == count_event.c.event_type_id)
            .outerjoin(instrument_family, instrument_family.c.id == count_event.c.family_id)
            .outerjoin(human_correction, human_correction.c.count_event_id == count_event.c.id)
        )
        .where(count_event.c.session_id == session_id)
        .order_by(count_event.c.occurred_at, EVENT_ORDER_RANK, count_event.c.id)
    ).mappings().all()
    return [dict(row) for row in rows]


def list_discrepancy_reason_codes(connection: Connection) -> set[str]:
    return set(connection.execute(select(cat_discrepancy_reason.c.code)).scalars().all())


def list_discrepancies(connection: Connection, session_id: uuid.UUID) -> list[dict[str, Any]]:
    rows = connection.execute(
        select(
            discrepancy.c.id,
            cat_discrepancy_reason.c.code.label("reason_code"),
            discrepancy.c.family_id,
            instrument_family.c.code.label("family_code"),
            discrepancy.c.expected_quantity,
            discrepancy.c.detected_quantity,
            discrepancy.c.resolved,
            discrepancy.c.resolved_at,
            discrepancy.c.origin_event_id,
            discrepancy.c.description,
        )
        .select_from(
            discrepancy.outerjoin(cat_discrepancy_reason, cat_discrepancy_reason.c.id == discrepancy.c.reason_id)
            .outerjoin(instrument_family, instrument_family.c.id == discrepancy.c.family_id)
        )
        .where(discrepancy.c.session_id == session_id)
        .order_by(
            discrepancy.c.resolved,  # open (false) first
            _c(instrument_family.c.code).asc().nulls_last(),
            discrepancy.c.id,
        )
    ).mappings().all()
    return [dict(row) for row in rows]
