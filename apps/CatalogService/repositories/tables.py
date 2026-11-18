"""Minimal SQLAlchemy Core mapping of the shared PostgreSQL tables Catalog Service reads/writes.

Only the columns this service needs are declared. These definitions mirror
data/migrations/001_init.sql and PEF-DS01/PEF-DS03; they are never used to create tables.
"""

from __future__ import annotations

from sqlalchemy import Boolean, Column, MetaData, SmallInteger, String, Table, Text
from sqlalchemy.dialects.postgresql import INET, TIMESTAMP, UUID

metadata = MetaData()

cat_instrument_category = Table(
    "cat_instrument_category",
    metadata,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("code", String(64), nullable=False),
    Column("name", String(120), nullable=False),
)

cat_instrument_cycle_status = Table(
    "cat_instrument_cycle_status",
    metadata,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("code", String(32), nullable=False),
    Column("name", String(80), nullable=False),
)

instrument_family = Table(
    "instrument_family",
    metadata,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("code", String(64), nullable=False),
    Column("name", String(160), nullable=False),
    Column("category_id", UUID(as_uuid=True), nullable=False),
    Column("identify_text", Text),
    Column("classify_text", Text),
    Column("function_text", Text),
    Column("active", Boolean, nullable=False),
)

instrument = Table(
    "instrument",
    metadata,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("internal_code", String(64)),
    Column("family_id", UUID(as_uuid=True), nullable=False),
    Column("cycle_status_id", UUID(as_uuid=True), nullable=False),
    Column("institution_id", UUID(as_uuid=True), nullable=False),
    Column("active", Boolean, nullable=False),
    Column("created_at", TIMESTAMP(timezone=True)),
    Column("updated_at", TIMESTAMP(timezone=True)),
)

kit = Table(
    "kit",
    metadata,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("name", String(160), nullable=False),
    Column("version", SmallInteger, nullable=False),
    Column("active", Boolean, nullable=False),
    Column("institution_id", UUID(as_uuid=True), nullable=False),
    Column("created_at", TIMESTAMP(timezone=True)),
    Column("updated_at", TIMESTAMP(timezone=True)),
)

kit_item = Table(
    "kit_item",
    metadata,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("kit_id", UUID(as_uuid=True), nullable=False),
    Column("family_id", UUID(as_uuid=True), nullable=False),
    Column("quantity", SmallInteger, nullable=False),
)

institution = Table(
    "institution",
    metadata,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("active", Boolean, nullable=False),
)

user = Table(
    "user",
    metadata,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("active", Boolean, nullable=False),
    Column("institution_id", UUID(as_uuid=True), nullable=False),
)

role = Table(
    "role",
    metadata,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("code", String(64), nullable=False),
    Column("institution_id", UUID(as_uuid=True), nullable=False),
)

user_role = Table(
    "user_role",
    metadata,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("user_id", UUID(as_uuid=True), nullable=False),
    Column("role_id", UUID(as_uuid=True), nullable=False),
)

# --- Written by the Catalog management endpoints (DS03 owner tables; DS07 audit) -------------

instrument_cycle_event = Table(
    "instrument_cycle_event",
    metadata,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("instrument_id", UUID(as_uuid=True), nullable=False),
    Column("cycle_status_id", UUID(as_uuid=True), nullable=False),
    Column("occurred_at", TIMESTAMP(timezone=True), nullable=False),
    Column("session_id", UUID(as_uuid=True)),
    Column("operation_id", UUID(as_uuid=True)),
    Column("notes", Text),
)

procedure_kit = Table(
    "procedure_kit",
    metadata,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("procedure_type_id", UUID(as_uuid=True), nullable=False),
    Column("kit_id", UUID(as_uuid=True), nullable=False),
    Column("is_default", Boolean, nullable=False),
    Column("active", Boolean, nullable=False),
    Column("updated_at", TIMESTAMP(timezone=True)),
)

# DS02 catalog, read only (procedures linked to a kit are shown with it).
cat_procedure_type = Table(
    "cat_procedure_type",
    metadata,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("code", String(64), nullable=False),
    Column("name", String(200), nullable=False),
)

access_audit = Table(
    "access_audit",
    metadata,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("occurred_at", TIMESTAMP(timezone=True), nullable=False),
    Column("actor_type", String(16), nullable=False),
    Column("actor_user_id", UUID(as_uuid=True)),
    Column("action", String(64), nullable=False),
    Column("resource_type", String(64), nullable=False),
    Column("resource_id", UUID(as_uuid=True), nullable=False),
    Column("institution_id", UUID(as_uuid=True)),
    Column("outcome", String(16), nullable=False),
    Column("correlation_id", String(64)),
    Column("ip", INET),
)
