"""PostgreSQL access for Catalog Service.

Every function receives an open Connection; transaction boundaries belong to the caller
(services). String ordering uses COLLATE "C" so the order is byte-wise and identical in
every environment, independent of the database's locale collation.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import and_, collate, select
from sqlalchemy.engine import Connection

from repositories.tables import (
    cat_instrument_category,
    cat_instrument_cycle_status,
    institution,
    instrument,
    instrument_family,
    kit,
    kit_item,
    role,
    user,
    user_role,
)


def _c(column):
    return collate(column, "C")


# --- Identity (read-only, used by auth) -------------------------------------------------


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
    """Effective role codes: all user_role rows whose role belongs to the user's institution."""
    rows = connection.execute(
        select(role.c.code)
        .select_from(user_role.join(role, role.c.id == user_role.c.role_id))
        .where(and_(user_role.c.user_id == user_id, role.c.institution_id == institution_id))
        .order_by(_c(role.c.code))
    ).scalars().all()
    return list(rows)


# --- Families (global catalog) ----------------------------------------------------------


def _family_select():
    return select(
        instrument_family.c.id,
        instrument_family.c.code,
        instrument_family.c.name,
        cat_instrument_category.c.code.label("category_code"),
        instrument_family.c.active,
    ).select_from(
        instrument_family.join(
            cat_instrument_category,
            cat_instrument_category.c.id == instrument_family.c.category_id,
        )
    )


def list_families(connection: Connection) -> list[dict[str, Any]]:
    rows = connection.execute(
        _family_select().order_by(_c(instrument_family.c.code), instrument_family.c.id)
    ).mappings().all()
    return [dict(row) for row in rows]


def get_family(connection: Connection, family_id: uuid.UUID) -> dict[str, Any] | None:
    row = connection.execute(
        _family_select().where(instrument_family.c.id == family_id)
    ).mappings().first()
    return dict(row) if row else None


def get_category_id(connection: Connection, category_code: str) -> uuid.UUID | None:
    return connection.execute(
        select(cat_instrument_category.c.id).where(cat_instrument_category.c.code == category_code)
    ).scalar_one_or_none()


def family_code_exists(connection: Connection, code: str) -> bool:
    found = connection.execute(
        select(instrument_family.c.id).where(instrument_family.c.code == code).limit(1)
    ).first()
    return found is not None


def insert_family(connection: Connection, values: dict[str, Any]) -> uuid.UUID:
    return connection.execute(
        instrument_family.insert().values(**values).returning(instrument_family.c.id)
    ).scalar_one()


# --- Instruments (tenant-scoped) --------------------------------------------------------


def list_instruments(connection: Connection, institution_id: uuid.UUID) -> list[dict[str, Any]]:
    rows = connection.execute(
        select(
            instrument.c.id,
            instrument.c.internal_code,
            instrument.c.family_id,
            instrument_family.c.code.label("family_code"),
            cat_instrument_cycle_status.c.code.label("cycle_status_code"),
            instrument.c.institution_id,
            instrument.c.active,
        )
        .select_from(
            instrument.join(instrument_family, instrument_family.c.id == instrument.c.family_id).join(
                cat_instrument_cycle_status,
                cat_instrument_cycle_status.c.id == instrument.c.cycle_status_id,
            )
        )
        .where(instrument.c.institution_id == institution_id)
        .order_by(
            _c(instrument_family.c.code),
            _c(instrument.c.internal_code).asc().nulls_last(),
            instrument.c.id,
        )
    ).mappings().all()
    return [dict(row) for row in rows]


# --- Kits (tenant-scoped) ---------------------------------------------------------------


def list_kits(connection: Connection, institution_id: uuid.UUID) -> list[dict[str, Any]]:
    rows = connection.execute(
        select(kit.c.id, kit.c.name, kit.c.version, kit.c.active, kit.c.institution_id)
        .where(kit.c.institution_id == institution_id)
        .order_by(_c(kit.c.name), kit.c.version, kit.c.id)
    ).mappings().all()
    return [dict(row) for row in rows]


def get_kit(connection: Connection, kit_id: uuid.UUID, institution_id: uuid.UUID) -> dict[str, Any] | None:
    """Tenant filter is part of the WHERE clause: another institution's kit is simply not found."""
    row = connection.execute(
        select(kit.c.id, kit.c.name, kit.c.version, kit.c.active, kit.c.institution_id).where(
            and_(kit.c.id == kit_id, kit.c.institution_id == institution_id)
        )
    ).mappings().first()
    return dict(row) if row else None


def list_kit_items(connection: Connection, kit_id: uuid.UUID) -> list[dict[str, Any]]:
    rows = connection.execute(
        select(
            kit_item.c.family_id,
            instrument_family.c.code.label("family_code"),
            kit_item.c.quantity,
        )
        .select_from(kit_item.join(instrument_family, instrument_family.c.id == kit_item.c.family_id))
        .where(kit_item.c.kit_id == kit_id)
        .order_by(_c(instrument_family.c.code), kit_item.c.family_id)
    ).mappings().all()
    return [dict(row) for row in rows]
