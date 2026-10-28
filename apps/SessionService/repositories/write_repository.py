"""PostgreSQL access for Session writes (Phase C: create, expected inventory, start;
Phase D: vision-counts, count-report; Phase E: resolve, close).

Every function runs on the caller's connection inside the caller's transaction. Lock helpers use
SELECT ... FOR UPDATE OF <row> so concurrent writers on the same operation/session serialize.
DS04 tables are written here (work_session, expected_inventory, count_event, human_correction,
discrepancy). DS03 exception (DS09 §1.3.4 rule 6, transitional): close releases the operation's
reservations and moves their pieces to sterilization (instrument, instrument_reservation,
instrument_cycle_event), always scoped to the session's institution. Other tables are read. `access_audit` is written exclusively by adapters/audit_adapter.py.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import and_, func, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import Connection

from repositories.tables import (
    capture_station,
    cat_discrepancy_reason,
    cat_event_type,
    cat_instrument_cycle_status,
    cat_operation_phase,
    cat_operation_status,
    cat_session_status,
    count_event,
    discrepancy,
    expected_inventory,
    human_correction,
    instrument,
    instrument_cycle_event,
    instrument_family,
    instrument_reservation,
    kit,
    kit_item,
    operating_room,
    operation,
    privacy_notice_version,
    session_processing_agreement,
    work_session,
)

AVAILABLE_CYCLE_STATUS = "available"


# --- Catalog lookups (codes are resolved, never hard-coded UUIDs) --------------------------


def session_status_id(connection: Connection, code: str) -> uuid.UUID:
    return connection.execute(select(cat_session_status.c.id).where(cat_session_status.c.code == code)).scalar_one()


def event_type_id(connection: Connection, code: str) -> uuid.UUID:
    return connection.execute(select(cat_event_type.c.id).where(cat_event_type.c.code == code)).scalar_one()


def get_phase(connection: Connection, code: str) -> dict[str, Any] | None:
    row = connection.execute(
        select(cat_operation_phase.c.id, cat_operation_phase.c.code, cat_operation_phase.c.active)
        .where(cat_operation_phase.c.code == code)
    ).mappings().first()
    return dict(row) if row else None


def get_phase_by_id(connection: Connection, phase_id: uuid.UUID) -> dict[str, Any] | None:
    row = connection.execute(
        select(cat_operation_phase.c.id, cat_operation_phase.c.code).where(cat_operation_phase.c.id == phase_id)
    ).mappings().first()
    return dict(row) if row else None


# --- References for POST /sessions ---------------------------------------------------------


def lock_operation(connection: Connection, operation_id: uuid.UUID) -> dict[str, Any] | None:
    """Lock the operation row alone (see lock_session for why), then read its status."""
    locked = connection.execute(
        select(operation.c.id).where(operation.c.id == operation_id).with_for_update()
    ).first()
    if locked is None:
        return None
    row = connection.execute(
        select(
            operation.c.id,
            operation.c.institution_id,
            operation.c.room_id,
            cat_operation_status.c.code.label("status_code"),
        )
        .select_from(operation.join(cat_operation_status, cat_operation_status.c.id == operation.c.status_id))
        .where(operation.c.id == operation_id)
    ).mappings().first()
    return dict(row) if row else None


def get_station(connection: Connection, station_id: uuid.UUID) -> dict[str, Any] | None:
    row = connection.execute(
        select(
            capture_station.c.id,
            capture_station.c.active,
            capture_station.c.room_id,
            operating_room.c.institution_id.label("room_institution_id"),
        )
        .select_from(capture_station.join(operating_room, operating_room.c.id == capture_station.c.room_id))
        .where(capture_station.c.id == station_id)
    ).mappings().first()
    return dict(row) if row else None


def get_kit(connection: Connection, kit_id: uuid.UUID) -> dict[str, Any] | None:
    row = connection.execute(
        select(kit.c.id, kit.c.active, kit.c.institution_id).where(kit.c.id == kit_id)
    ).mappings().first()
    return dict(row) if row else None


def kit_quantities(connection: Connection, kit_id: uuid.UUID | None) -> dict[uuid.UUID, int]:
    if kit_id is None:
        return {}
    rows = connection.execute(select(kit_item.c.family_id, kit_item.c.quantity).where(kit_item.c.kit_id == kit_id))
    return {row.family_id: int(row.quantity) for row in rows}


def active_family_ids(connection: Connection, family_ids: list[uuid.UUID]) -> set[uuid.UUID]:
    if not family_ids:
        return set()
    rows = connection.execute(
        select(instrument_family.c.id).where(
            and_(instrument_family.c.id.in_(family_ids), instrument_family.c.active.is_(True))
        )
    ).scalars()
    return set(rows)


def available_stock(
    connection: Connection, family_ids: list[uuid.UUID], institution_id: uuid.UUID
) -> dict[uuid.UUID, int]:
    """Pieces that count as available: same family, same institution, active, cycle `available`.

    Same criteria as the tenant-safe portal check. This is a point-in-time validation, NOT a
    reservation: availability can change after the transaction commits.
    """
    if not family_ids:
        return {}
    rows = connection.execute(
        select(instrument.c.family_id, func.count(instrument.c.id))
        .select_from(
            instrument.join(cat_instrument_cycle_status, cat_instrument_cycle_status.c.id == instrument.c.cycle_status_id)
        )
        .where(
            and_(
                instrument.c.family_id.in_(family_ids),
                instrument.c.institution_id == institution_id,
                instrument.c.active.is_(True),
                cat_instrument_cycle_status.c.code == AVAILABLE_CYCLE_STATUS,
            )
        )
        .group_by(instrument.c.family_id)
    )
    return {row[0]: int(row[1]) for row in rows}


# --- Sessions ------------------------------------------------------------------------------


def insert_session(connection: Connection, values: dict[str, Any]) -> None:
    connection.execute(work_session.insert().values(**values))


def lock_session(connection: Connection, session_id: uuid.UUID) -> dict[str, Any] | None:
    """Locks the work_session row; sessions without operation have no tenant and are not found.

    Two statements on purpose: the row is locked ALONE first. With a join inside FOR UPDATE,
    PostgreSQL re-checks a concurrently updated row against the join rows it read before waiting
    (EvalPlanQual), so a session whose status changed meanwhile would vanish from the result
    (404 instead of 409). Once the lock is held, the joined read sees the committed state.
    """
    locked = connection.execute(
        select(work_session.c.id).where(work_session.c.id == session_id).with_for_update()
    ).first()
    if locked is None:
        return None
    row = connection.execute(
        select(
            work_session.c.id,
            work_session.c.user_id,
            work_session.c.kit_id,
            work_session.c.capture_mode,
            work_session.c.current_phase_id,
            work_session.c.operation_id,
            work_session.c.ended_at,
            cat_session_status.c.code.label("status_code"),
            operation.c.institution_id,
        )
        .select_from(
            work_session.join(operation, operation.c.id == work_session.c.operation_id).join(
                cat_session_status, cat_session_status.c.id == work_session.c.status_id
            )
        )
        .where(work_session.c.id == session_id)
    ).mappings().first()
    return dict(row) if row else None


def update_session(connection: Connection, session_id: uuid.UUID, values: dict[str, Any]) -> None:
    connection.execute(work_session.update().where(work_session.c.id == session_id).values(**values))


def has_valid_agreement(connection: Connection, session_id: uuid.UUID, now: datetime) -> bool:
    """DS07: an agreement for the session with quality_ops and a notice already in force."""
    found = connection.execute(
        select(session_processing_agreement.c.id)
        .select_from(
            session_processing_agreement.join(
                privacy_notice_version,
                privacy_notice_version.c.id == session_processing_agreement.c.privacy_notice_version_id,
            )
        )
        .where(
            and_(
                session_processing_agreement.c.session_id == session_id,
                session_processing_agreement.c.purpose_quality_ops.is_(True),
                privacy_notice_version.c.effective_at <= now,
            )
        )
    ).first()
    return found is not None


# --- Expected inventory ----------------------------------------------------------------------


def expected_rows(connection: Connection, session_id: uuid.UUID) -> dict[uuid.UUID, dict[str, Any]]:
    rows = connection.execute(
        select(expected_inventory.c.id, expected_inventory.c.family_id, expected_inventory.c.expected_quantity,
               expected_inventory.c.source)
        .where(expected_inventory.c.session_id == session_id)
    ).mappings().all()
    return {row["family_id"]: dict(row) for row in rows}


def insert_expected_line(connection: Connection, session_id: uuid.UUID, family_id: uuid.UUID, quantity: int,
                         source: str) -> None:
    connection.execute(expected_inventory.insert().values(
        id=uuid.uuid4(), session_id=session_id, family_id=family_id, expected_quantity=quantity, source=source,
    ))


def update_expected_line(connection: Connection, line_id: uuid.UUID, quantity: int, source: str) -> None:
    connection.execute(
        expected_inventory.update().where(expected_inventory.c.id == line_id)
        .values(expected_quantity=quantity, source=source)
    )


def delete_expected_lines(connection: Connection, line_ids: list[uuid.UUID]) -> None:
    if line_ids:
        connection.execute(expected_inventory.delete().where(expected_inventory.c.id.in_(line_ids)))


# --- Count events ---------------------------------------------------------------------------


def insert_count_event(connection: Connection, values: dict[str, Any]) -> uuid.UUID:
    event_id = uuid.uuid4()
    connection.execute(count_event.insert().values(id=event_id, **values))
    return event_id


def find_count_event_by_client_id(connection: Connection, client_event_id: uuid.UUID) -> dict[str, Any] | None:
    """The committed event that owns this client_event_id (UNIQUE), whatever its session/type."""
    row = connection.execute(
        select(
            count_event.c.id,
            count_event.c.session_id,
            count_event.c.occurred_at,
            count_event.c.payload,
            cat_event_type.c.code.label("event_type_code"),
        )
        .select_from(count_event.join(cat_event_type, cat_event_type.c.id == count_event.c.event_type_id))
        .where(count_event.c.client_event_id == client_event_id)
    ).mappings().first()
    return dict(row) if row else None


def insert_auto_count(connection: Connection, values: dict[str, Any]) -> uuid.UUID | None:
    """INSERT ... ON CONFLICT (client_event_id) DO NOTHING RETURNING id.

    The UNIQUE constraint is the final idempotency guard: if another transaction inserted (or is
    inserting) the same client_event_id, PostgreSQL waits for it and then skips the row → None.
    """
    event_id = uuid.uuid4()
    inserted = connection.execute(
        pg_insert(count_event)
        .values(id=event_id, **values)
        .on_conflict_do_nothing(index_elements=[count_event.c.client_event_id])
        .returning(count_event.c.id)
    ).scalar()
    return inserted


def latest_final_auto_count(connection: Connection, session_id: uuid.UUID) -> dict[str, Any] | None:
    """Last auto_count with trigger = final: occurred_at DESC, id DESC as stable tie-break.

    stable_window batches are intermediate and never returned here.
    """
    row = connection.execute(
        select(count_event.c.id, count_event.c.payload)
        .select_from(count_event.join(cat_event_type, cat_event_type.c.id == count_event.c.event_type_id))
        .where(
            and_(
                count_event.c.session_id == session_id,
                cat_event_type.c.code == "auto_count",
                count_event.c.payload["trigger"].astext == "final",
            )
        )
        .order_by(count_event.c.occurred_at.desc(), count_event.c.id.desc())
        .limit(1)
    ).mappings().first()
    return dict(row) if row else None


# --- Count report: human corrections and discrepancies ------------------------------------------


def discrepancy_reason_ids(connection: Connection) -> dict[str, uuid.UUID]:
    rows = connection.execute(select(cat_discrepancy_reason.c.code, cat_discrepancy_reason.c.id))
    return {row.code: row.id for row in rows}


def family_names(connection: Connection, family_ids: list[uuid.UUID]) -> dict[uuid.UUID, str]:
    if not family_ids:
        return {}
    rows = connection.execute(
        select(instrument_family.c.id, instrument_family.c.name).where(instrument_family.c.id.in_(family_ids))
    )
    return {row.id: row.name for row in rows}


def insert_human_correction(connection: Connection, values: dict[str, Any]) -> None:
    connection.execute(human_correction.insert().values(id=uuid.uuid4(), **values))


def insert_discrepancy(connection: Connection, values: dict[str, Any]) -> None:
    connection.execute(discrepancy.insert().values(id=uuid.uuid4(), **values))


def count_discrepancies(connection: Connection, session_id: uuid.UUID) -> int:
    """Every discrepancy of the session, open or resolved."""
    return int(connection.execute(
        select(func.count(discrepancy.c.id)).where(discrepancy.c.session_id == session_id)
    ).scalar_one())


def count_open_discrepancies(connection: Connection, session_id: uuid.UUID) -> int:
    return int(connection.execute(
        select(func.count(discrepancy.c.id)).where(
            and_(discrepancy.c.session_id == session_id, discrepancy.c.resolved.is_(False))
        )
    ).scalar_one())


# --- Phase E: discrepancy resolution ---------------------------------------------------------------


def lock_discrepancy(connection: Connection, discrepancy_id: uuid.UUID, session_id: uuid.UUID) -> dict[str, Any] | None:
    """Locks the discrepancy only if it belongs to the (already locked) session; single table, no join."""
    row = connection.execute(
        select(discrepancy.c.id, discrepancy.c.resolved, discrepancy.c.description, discrepancy.c.family_id,
               discrepancy.c.reason_id)
        .where(and_(discrepancy.c.id == discrepancy_id, discrepancy.c.session_id == session_id))
        .with_for_update()
    ).mappings().first()
    return dict(row) if row else None


def discrepancy_reason_code(connection: Connection, reason_id: uuid.UUID | None) -> str | None:
    if reason_id is None:
        return None
    return connection.execute(text("SELECT code FROM cat_discrepancy_reason WHERE id = :id"), {"id": reason_id}).scalar()


def physical_shortage_ids(connection: Connection, session_id: uuid.UUID) -> list[uuid.UUID]:
    """Discrepancies of the session that are a confirmed physical shortage (reason `shortage`)."""
    return list(connection.execute(text("""
        SELECT d.id FROM discrepancy d JOIN cat_discrepancy_reason r ON r.id = d.reason_id
        WHERE d.session_id = :s AND r.code = 'shortage' ORDER BY d.id
    """), {"s": session_id}).scalars())


def update_discrepancy(connection: Connection, discrepancy_id: uuid.UUID, values: dict[str, Any]) -> None:
    connection.execute(discrepancy.update().where(discrepancy.c.id == discrepancy_id).values(**values))


# --- Phase E: close side effects (DS03, transitional) ----------------------------------------------


def cycle_status_ids(connection: Connection) -> dict[str, uuid.UUID]:
    rows = connection.execute(select(cat_instrument_cycle_status.c.code, cat_instrument_cycle_status.c.id))
    return {row.code: row.id for row in rows}


def scoped_active_reservations(
    connection: Connection, operation_id: uuid.UUID, institution_id: uuid.UUID
) -> list[dict[str, Any]]:
    """Candidate reservations (plain read): active, of this operation, piece of this institution."""
    rows = connection.execute(
        select(instrument_reservation.c.id, instrument_reservation.c.instrument_id)
        .select_from(instrument_reservation.join(instrument, instrument.c.id == instrument_reservation.c.instrument_id))
        .where(
            and_(
                instrument_reservation.c.operation_id == operation_id,
                instrument_reservation.c.active.is_(True),
                instrument.c.institution_id == institution_id,
            )
        )
        .order_by(instrument_reservation.c.id)
    ).mappings().all()
    return [dict(row) for row in rows]


def active_reservation_ids_of_instrument(
    connection: Connection, instrument_id: uuid.UUID, institution_id: uuid.UUID
) -> list[uuid.UUID]:
    """Active reservations of exactly this piece whose operation belongs to the institution (plain read)."""
    rows = connection.execute(
        select(instrument_reservation.c.id)
        .select_from(instrument_reservation.join(operation, operation.c.id == instrument_reservation.c.operation_id))
        .where(
            and_(
                instrument_reservation.c.instrument_id == instrument_id,
                instrument_reservation.c.active.is_(True),
                operation.c.institution_id == institution_id,
            )
        )
        .order_by(instrument_reservation.c.id)
    ).scalars()
    return list(rows)


def lock_reservations(connection: Connection, reservation_ids: list[uuid.UUID]) -> list[dict[str, Any]]:
    """Locks the reservation rows alone in id order (deterministic, deadlock-free), then returns their
    committed state; the caller re-checks scope because it may have changed while waiting."""
    if not reservation_ids:
        return []
    connection.execute(
        select(instrument_reservation.c.id)
        .where(instrument_reservation.c.id.in_(reservation_ids))
        .order_by(instrument_reservation.c.id)
        .with_for_update()
    ).all()
    rows = connection.execute(
        select(instrument_reservation.c.id, instrument_reservation.c.operation_id, instrument_reservation.c.instrument_id,
               instrument_reservation.c.active)
        .where(instrument_reservation.c.id.in_(reservation_ids))
        .order_by(instrument_reservation.c.id)
    ).mappings().all()
    return [dict(row) for row in rows]


def lock_instruments(connection: Connection, instrument_ids: list[uuid.UUID]) -> dict[uuid.UUID, dict[str, Any]]:
    """Locks the instrument rows alone in id order, then reads their current cycle status.

    Global lock order (DS03 §1.3.9.1): work_session → discrepancy → instrument → instrument_reservation.
    """
    if not instrument_ids:
        return {}
    connection.execute(
        select(instrument.c.id).where(instrument.c.id.in_(instrument_ids)).order_by(instrument.c.id).with_for_update()
    ).all()
    rows = connection.execute(
        select(instrument.c.id, instrument.c.institution_id, instrument.c.active, instrument.c.family_id,
               cat_instrument_cycle_status.c.code.label("cycle_status_code"))
        .select_from(
            instrument.join(cat_instrument_cycle_status, cat_instrument_cycle_status.c.id == instrument.c.cycle_status_id)
        )
        .where(instrument.c.id.in_(instrument_ids))
    ).mappings().all()
    return {row["id"]: dict(row) for row in rows}


def release_reservation(connection: Connection, reservation_id: uuid.UUID, released_at: datetime) -> None:
    connection.execute(
        instrument_reservation.update().where(instrument_reservation.c.id == reservation_id)
        .values(active=False, released_at=released_at)
    )


def update_instrument_cycle(connection: Connection, instrument_id: uuid.UUID, cycle_status_id: uuid.UUID,
                            updated_at: datetime, *, active: bool | None = None) -> None:
    values: dict[str, Any] = {"cycle_status_id": cycle_status_id, "updated_at": updated_at}
    if active is not None:
        values["active"] = active
    connection.execute(instrument.update().where(instrument.c.id == instrument_id).values(**values))


def insert_cycle_event(connection: Connection, values: dict[str, Any]) -> None:
    connection.execute(instrument_cycle_event.insert().values(id=uuid.uuid4(), **values))
