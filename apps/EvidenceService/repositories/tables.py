"""Minimal SQLAlchemy Core mapping of the PostgreSQL tables Evidence Service uses.

Identity (auth context), media_asset (PEF-DS05 v0.2.0, incl. session_id from migration 004),
work_session/operation/cat_session_status (tenant, operator scope, upload state; read-only) and access_audit (written only by
adapters/audit_adapter.py). Mirrors data/migrations/001_init.sql + 004; never used to create or
alter tables (no metadata.create_all).
"""

from __future__ import annotations

from sqlalchemy import BigInteger, Boolean, CHAR, Column, MetaData, String, Table, Text
from sqlalchemy.dialects.postgresql import TIMESTAMP, UUID

metadata = MetaData()

institution = Table(
    "institution", metadata,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("active", Boolean, nullable=False),
)

user = Table(
    "user", metadata,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("active", Boolean, nullable=False),
    Column("institution_id", UUID(as_uuid=True), nullable=False),
)

role = Table(
    "role", metadata,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("code", String(64), nullable=False),
    Column("institution_id", UUID(as_uuid=True), nullable=False),
)

user_role = Table(
    "user_role", metadata,
    Column("user_id", UUID(as_uuid=True), primary_key=True),
    Column("role_id", UUID(as_uuid=True), primary_key=True),
)

# --- PEF-DS05: evidence metadata (owned) ---------------------------------------------------

media_asset = Table(
    "media_asset", metadata,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("session_id", UUID(as_uuid=True)),
    Column("gcs_uri", Text, nullable=False),
    Column("bucket", String(128), nullable=False),
    Column("object_key", Text, nullable=False),
    Column("content_type", String(128)),
    Column("kind", String(32), nullable=False),
    Column("storage_provider", String(32), nullable=False),
    Column("sha256", CHAR(64)),
    Column("size_bytes", BigInteger),
    Column("retention_until", TIMESTAMP(timezone=True)),
    Column("purge_requested_at", TIMESTAMP(timezone=True)),
    Column("purged_at", TIMESTAMP(timezone=True)),
    Column("blocked_at", TIMESTAMP(timezone=True)),
    Column("created_at", TIMESTAMP(timezone=True), nullable=False),
    Column("updated_at", TIMESTAMP(timezone=True), nullable=False),
)

# --- PEF-DS04 / DS02: session scope (read-only) --------------------------------------------

work_session = Table(
    "work_session", metadata,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("status_id", UUID(as_uuid=True), nullable=False),
    Column("user_id", UUID(as_uuid=True), nullable=False),
    Column("operation_id", UUID(as_uuid=True)),
    Column("capture_mode", String(32)),
    Column("extended_retention", Boolean, nullable=False),
)

cat_session_status = Table(
    "cat_session_status", metadata,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("code", String(32), nullable=False),
)

operation = Table(
    "operation", metadata,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("institution_id", UUID(as_uuid=True), nullable=False),
)

# --- PEF-DS07: transitional audit (written only by adapters/audit_adapter.py) ----------------

access_audit = Table(
    "access_audit", metadata,
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
)
