"""PostgreSQL access of the Catalog management endpoints (detail views and writes).

Same conventions as catalog_repository: every function receives an open Connection, the caller
owns the transaction, byte-wise ordering (COLLATE "C"). Tenant-scoped data (instruments, kits,
available counts, kits using a family) always filters by the caller's institution.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from datetime import datetime
from typing import Any

from sqlalchemy import and_, collate, func, select, update
from sqlalchemy.engine import Connection

from repositories.tables import (
    cat_instrument_category,
    cat_instrument_cycle_status,
    cat_procedure_type,
    instrument,
    instrument_cycle_event,
    instrument_family,
    kit,
    kit_item,
    procedure_kit,
)

AVAILABLE_STATUS_CODE = "available"


def _c(column):
    return collate(column, "C")


# --- Reference catalogs (DS03) -------------------------------------------------------------


def list_categories(connection: Connection) -> list[dict[str, Any]]:
    rows = connection.execute(select(cat_instrument_category.c.id, cat_instrument_category.c.code,
                                     cat_instrument_category.c.name)
                              .order_by(_c(cat_instrument_category.c.code))).mappings().all()
    return [dict(row) for row in rows]


def list_cycle_statuses(connection: Connection) -> list[dict[str, Any]]:
    rows = connection.execute(select(cat_instrument_cycle_status.c.id, cat_instrument_cycle_status.c.code,
                                     cat_instrument_cycle_status.c.name)
                              .order_by(_c(cat_instrument_cycle_status.c.code))).mappings().all()
    return [dict(row) for row in rows]


def get_category_by_code(connection: Connection, code: str) -> dict[str, Any] | None:
    row = connection.execute(select(cat_instrument_category.c.id, cat_instrument_category.c.code)
                             .where(cat_instrument_category.c.code == code)).mappings().first()
    return dict(row) if row else None


def get_cycle_status_by_code(connection: Connection, code: str) -> dict[str, Any] | None:
    row = connection.execute(select(cat_instrument_cycle_status.c.id, cat_instrument_cycle_status.c.code)
                             .where(cat_instrument_cycle_status.c.code == code)).mappings().first()
    return dict(row) if row else None


# --- Families ------------------------------------------------------------------------------


def _family_detail_select():
    return select(
        instrument_family.c.id, instrument_family.c.code, instrument_family.c.name,
        instrument_family.c.identify_text, instrument_family.c.classify_text, instrument_family.c.function_text,
        instrument_family.c.active, instrument_family.c.category_id,
        cat_instrument_category.c.code.label("category_code"), cat_instrument_category.c.name.label("category_name"),
    ).select_from(instrument_family.join(cat_instrument_category,
                                         cat_instrument_category.c.id == instrument_family.c.category_id))


def list_families_detail(connection: Connection) -> list[dict[str, Any]]:
    rows = connection.execute(_family_detail_select().order_by(_c(instrument_family.c.code),
                                                               instrument_family.c.id)).mappings().all()
    return [dict(row) for row in rows]


def get_family_detail(connection: Connection, family_id: uuid.UUID, *, for_update: bool = False) -> dict[str, Any] | None:
    query = _family_detail_select().where(instrument_family.c.id == family_id)
    if for_update:
        query = query.with_for_update(of=instrument_family)
    row = connection.execute(query).mappings().first()
    return dict(row) if row else None


def update_family(connection: Connection, family_id: uuid.UUID, values: dict[str, Any]) -> None:
    connection.execute(update(instrument_family).where(instrument_family.c.id == family_id).values(**values))


def available_counts(connection: Connection, institution_id: uuid.UUID) -> dict[uuid.UUID, int]:
    """Active pieces of the caller's institution currently in cycle status `available`, per family."""
    rows = connection.execute(
        select(instrument.c.family_id, func.count(instrument.c.id))
        .select_from(instrument.join(cat_instrument_cycle_status,
                                     cat_instrument_cycle_status.c.id == instrument.c.cycle_status_id))
        .where(and_(instrument.c.institution_id == institution_id, instrument.c.active.is_(True),
                    cat_instrument_cycle_status.c.code == AVAILABLE_STATUS_CODE))
        .group_by(instrument.c.family_id)
    ).all()
    return {family_id: int(count) for family_id, count in rows}


def kits_using_family(connection: Connection, family_id: uuid.UUID, institution_id: uuid.UUID) -> int:
    return int(connection.execute(
        select(func.count()).select_from(kit_item.join(kit, kit.c.id == kit_item.c.kit_id))
        .where(and_(kit_item.c.family_id == family_id, kit.c.active.is_(True), kit.c.institution_id == institution_id))
    ).scalar() or 0)


def existing_family_ids(connection: Connection, family_ids) -> set[uuid.UUID]:
    if not family_ids:
        return set()
    return set(connection.execute(select(instrument_family.c.id).where(instrument_family.c.id.in_(list(family_ids))))
               .scalars())


# --- Instruments ---------------------------------------------------------------------------


def _instrument_detail_select():
    return select(
        instrument.c.id, instrument.c.internal_code, instrument.c.family_id, instrument.c.cycle_status_id,
        instrument.c.institution_id, instrument.c.active,
        instrument_family.c.code.label("family_code"), instrument_family.c.name.label("family_name"),
        cat_instrument_category.c.code.label("category_code"), cat_instrument_category.c.name.label("category_name"),
        cat_instrument_cycle_status.c.code.label("cycle_status_code"),
        cat_instrument_cycle_status.c.name.label("cycle_status_name"),
    ).select_from(
        instrument.join(instrument_family, instrument_family.c.id == instrument.c.family_id)
        .join(cat_instrument_category, cat_instrument_category.c.id == instrument_family.c.category_id)
        .join(cat_instrument_cycle_status, cat_instrument_cycle_status.c.id == instrument.c.cycle_status_id)
    )


def list_instruments_detail(connection: Connection, institution_id: uuid.UUID) -> list[dict[str, Any]]:
    rows = connection.execute(
        _instrument_detail_select().where(instrument.c.institution_id == institution_id).order_by(
            _c(instrument_family.c.code), _c(instrument.c.internal_code).asc().nulls_last(), instrument.c.id)
    ).mappings().all()
    return [dict(row) for row in rows]


def get_instrument_detail(connection: Connection, instrument_id: uuid.UUID, institution_id: uuid.UUID,
                          *, for_update: bool = False) -> dict[str, Any] | None:
    query = _instrument_detail_select().where(and_(instrument.c.id == instrument_id,
                                                   instrument.c.institution_id == institution_id))
    if for_update:
        query = query.with_for_update(of=instrument)
    row = connection.execute(query).mappings().first()
    return dict(row) if row else None


def list_cycle_events(connection: Connection, instrument_id: uuid.UUID, limit: int = 10) -> list[dict[str, Any]]:
    rows = connection.execute(
        select(instrument_cycle_event.c.occurred_at, instrument_cycle_event.c.notes,
               cat_instrument_cycle_status.c.code.label("cycle_status_code"),
               cat_instrument_cycle_status.c.name.label("cycle_status_name"))
        .select_from(instrument_cycle_event.join(
            cat_instrument_cycle_status, cat_instrument_cycle_status.c.id == instrument_cycle_event.c.cycle_status_id))
        .where(instrument_cycle_event.c.instrument_id == instrument_id)
        .order_by(instrument_cycle_event.c.occurred_at.desc(), instrument_cycle_event.c.id)
        .limit(limit)
    ).mappings().all()
    return [dict(row) for row in rows]


def insert_instrument(connection: Connection, values: dict[str, Any]) -> None:
    connection.execute(instrument.insert().values(**values))


def update_instrument(connection: Connection, instrument_id: uuid.UUID, values: dict[str, Any]) -> None:
    connection.execute(update(instrument).where(instrument.c.id == instrument_id).values(**values))


def insert_cycle_event(connection: Connection, *, instrument_id: uuid.UUID, cycle_status_id: uuid.UUID,
                       occurred_at: datetime, notes: str) -> None:
    connection.execute(instrument_cycle_event.insert().values(
        id=uuid.uuid4(), instrument_id=instrument_id, cycle_status_id=cycle_status_id,
        occurred_at=occurred_at, notes=notes))


# --- Kits ----------------------------------------------------------------------------------


def kit_rows(connection: Connection, institution_id: uuid.UUID) -> list[dict[str, Any]]:
    rows = connection.execute(
        select(kit.c.id, kit.c.name, kit.c.version, kit.c.active, kit.c.institution_id, kit.c.created_at)
        .where(kit.c.institution_id == institution_id)
        .order_by(_c(kit.c.name), kit.c.version, kit.c.id)
    ).mappings().all()
    return [dict(row) for row in rows]


def get_kit_row(connection: Connection, kit_id: uuid.UUID, institution_id: uuid.UUID,
                *, for_update: bool = False) -> dict[str, Any] | None:
    query = select(kit.c.id, kit.c.name, kit.c.version, kit.c.active, kit.c.institution_id, kit.c.created_at).where(
        and_(kit.c.id == kit_id, kit.c.institution_id == institution_id))
    if for_update:
        query = query.with_for_update()
    row = connection.execute(query).mappings().first()
    return dict(row) if row else None


def kit_item_totals(connection: Connection, kit_ids) -> dict[uuid.UUID, tuple[int, int]]:
    """kit_id -> (families, total expected pieces)."""
    if not kit_ids:
        return {}
    rows = connection.execute(
        select(kit_item.c.kit_id, func.count(kit_item.c.id), func.coalesce(func.sum(kit_item.c.quantity), 0))
        .where(kit_item.c.kit_id.in_(list(kit_ids))).group_by(kit_item.c.kit_id)
    ).all()
    return {kit_id: (int(families), int(total)) for kit_id, families, total in rows}


def procedures_by_kit(connection: Connection, kit_ids) -> dict[uuid.UUID, list[dict[str, Any]]]:
    """Procedures linked to each kit (procedure_kit, DS03) with their DS02 code and name."""
    result: dict[uuid.UUID, list[dict[str, Any]]] = defaultdict(list)
    if not kit_ids:
        return result
    rows = connection.execute(
        select(procedure_kit.c.kit_id, cat_procedure_type.c.id, cat_procedure_type.c.code, cat_procedure_type.c.name)
        .select_from(procedure_kit.join(cat_procedure_type, cat_procedure_type.c.id == procedure_kit.c.procedure_type_id))
        .where(procedure_kit.c.kit_id.in_(list(kit_ids)))
        .order_by(_c(cat_procedure_type.c.code))
    ).all()
    for kit_id, procedure_id, code, name in rows:
        result[kit_id].append({"id": str(procedure_id), "code": code, "name": name})
    return result


def kit_items_detail(connection: Connection, kit_id: uuid.UUID) -> list[dict[str, Any]]:
    rows = connection.execute(
        select(kit_item.c.family_id, kit_item.c.quantity, instrument_family.c.code.label("family_code"),
               instrument_family.c.name.label("family_name"), instrument_family.c.active.label("family_active"),
               cat_instrument_category.c.code.label("category_code"),
               cat_instrument_category.c.name.label("category_name"))
        .select_from(kit_item.join(instrument_family, instrument_family.c.id == kit_item.c.family_id)
                     .join(cat_instrument_category, cat_instrument_category.c.id == instrument_family.c.category_id))
        .where(kit_item.c.kit_id == kit_id)
        .order_by(_c(instrument_family.c.code), kit_item.c.family_id)
    ).mappings().all()
    return [dict(row) for row in rows]


def kit_composition(connection: Connection, kit_id: uuid.UUID) -> dict[uuid.UUID, int]:
    return {family_id: int(quantity) for family_id, quantity in connection.execute(
        select(kit_item.c.family_id, kit_item.c.quantity).where(kit_item.c.kit_id == kit_id)).all()}


def next_kit_version(connection: Connection, institution_id: uuid.UUID, name: str) -> int:
    current = connection.execute(
        select(func.max(kit.c.version)).where(and_(kit.c.institution_id == institution_id, kit.c.name == name))
    ).scalar()
    return int(current or 0) + 1


def insert_kit(connection: Connection, values: dict[str, Any], composition: dict[uuid.UUID, int]) -> None:
    connection.execute(kit.insert().values(**values))
    for family_id, quantity in composition.items():
        connection.execute(kit_item.insert().values(id=uuid.uuid4(), kit_id=values["id"], family_id=family_id,
                                                    quantity=quantity))


def supersede_kit(connection: Connection, old_kit_id: uuid.UUID, new_kit_id: uuid.UUID, now: datetime) -> int:
    """Deactivate the old version and move its procedure links to the new one; returns links moved."""
    connection.execute(update(kit).where(kit.c.id == old_kit_id).values(active=False, updated_at=now))
    result = connection.execute(update(procedure_kit).where(procedure_kit.c.kit_id == old_kit_id)
                                .values(kit_id=new_kit_id, updated_at=now))
    return int(result.rowcount or 0)
