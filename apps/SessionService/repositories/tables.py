"""Minimal SQLAlchemy Core mapping of the PostgreSQL tables Session Service reads/writes.

Only the columns this service needs are declared. They mirror data/migrations/001_init.sql and
PEF-DS01/02/03/04/07; they are never used to create or alter tables.
"""

from __future__ import annotations

from sqlalchemy import Boolean, Column, MetaData, SmallInteger, String, Table, Text
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP, UUID

metadata = MetaData()

# --- DS04: Session domain (owned) --------------------------------------------------------

cat_session_status = Table(
    "cat_session_status", metadata,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("code", String(32), nullable=False),
)

cat_event_type = Table(
    "cat_event_type", metadata,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("code", String(64), nullable=False),
)

cat_discrepancy_reason = Table(
    "cat_discrepancy_reason", metadata,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("code", String(64), nullable=False),
)

work_session = Table(
    "work_session", metadata,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("started_at", TIMESTAMP(timezone=True)),
    Column("ended_at", TIMESTAMP(timezone=True)),
    Column("status_id", UUID(as_uuid=True), nullable=False),
    Column("user_id", UUID(as_uuid=True), nullable=False),
    Column("closed_by_user_id", UUID(as_uuid=True)),
    Column("operation_id", UUID(as_uuid=True)),
    Column("station_id", UUID(as_uuid=True)),
    Column("kit_id", UUID(as_uuid=True)),
    Column("current_phase_id", UUID(as_uuid=True)),
    Column("phase_changed_at", TIMESTAMP(timezone=True)),
    Column("capture_mode", String(32)),
    Column("updated_at", TIMESTAMP(timezone=True), nullable=False),
)

expected_inventory = Table(
    "expected_inventory", metadata,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("family_id", UUID(as_uuid=True), nullable=False),
    Column("expected_quantity", SmallInteger, nullable=False),
    Column("session_id", UUID(as_uuid=True), nullable=False),
    Column("source", String(32), nullable=False),
)

count_event = Table(
    "count_event", metadata,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("event_type_id", UUID(as_uuid=True), nullable=False),
    Column("client_event_id", UUID(as_uuid=True)),
    Column("family_id", UUID(as_uuid=True)),
    Column("expected_quantity", SmallInteger),
    Column("detected_quantity", SmallInteger),
    Column("payload", JSONB, nullable=False),
    Column("occurred_at", TIMESTAMP(timezone=True), nullable=False),
    Column("session_id", UUID(as_uuid=True), nullable=False),
    Column("user_id", UUID(as_uuid=True)),
)

discrepancy = Table(
    "discrepancy", metadata,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("description", Text, nullable=False),
    Column("resolved", Boolean, nullable=False),
    Column("resolved_at", TIMESTAMP(timezone=True)),
    Column("reason_id", UUID(as_uuid=True)),
    Column("family_id", UUID(as_uuid=True)),
    Column("expected_quantity", SmallInteger),
    Column("detected_quantity", SmallInteger),
    Column("session_id", UUID(as_uuid=True), nullable=False),
    Column("origin_event_id", UUID(as_uuid=True)),
    Column("updated_at", TIMESTAMP(timezone=True), nullable=False),
)

human_correction = Table(
    "human_correction", metadata,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("justification", Text, nullable=False),
    Column("recorded_at", TIMESTAMP(timezone=True), nullable=False),
    Column("count_event_id", UUID(as_uuid=True), nullable=False),
    Column("user_id", UUID(as_uuid=True)),
)

# --- DS01: identity (read-only) -----------------------------------------------------------

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
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("user_id", UUID(as_uuid=True), nullable=False),
    Column("role_id", UUID(as_uuid=True), nullable=False),
)

# --- DS02 / DS03 / DS07 (read-only, only what the DS09 read contracts need) ---------------

operation = Table(
    "operation", metadata,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("scheduled_at", TIMESTAMP(timezone=True)),
    Column("status_id", UUID(as_uuid=True), nullable=False),
    Column("room_id", UUID(as_uuid=True)),
    Column("institution_id", UUID(as_uuid=True), nullable=False),
)

cat_operation_status = Table(
    "cat_operation_status", metadata,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("code", String(32), nullable=False),
)

operating_room = Table(
    "operating_room", metadata,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("active", Boolean, nullable=False),
    Column("institution_id", UUID(as_uuid=True), nullable=False),
)

capture_station = Table(
    "capture_station", metadata,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("active", Boolean, nullable=False),
    Column("room_id", UUID(as_uuid=True), nullable=False),
)

kit = Table(
    "kit", metadata,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("active", Boolean, nullable=False),
    Column("institution_id", UUID(as_uuid=True), nullable=False),
)

kit_item = Table(
    "kit_item", metadata,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("kit_id", UUID(as_uuid=True), nullable=False),
    Column("family_id", UUID(as_uuid=True), nullable=False),
    Column("quantity", SmallInteger, nullable=False),
)

instrument_family = Table(
    "instrument_family", metadata,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("code", String(64), nullable=False),
    Column("name", String(160), nullable=False),
    Column("active", Boolean, nullable=False),
)

instrument = Table(
    "instrument", metadata,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("family_id", UUID(as_uuid=True), nullable=False),
    Column("cycle_status_id", UUID(as_uuid=True), nullable=False),
    Column("institution_id", UUID(as_uuid=True), nullable=False),
    Column("active", Boolean, nullable=False),
    Column("updated_at", TIMESTAMP(timezone=True), nullable=False),
)

instrument_reservation = Table(
    "instrument_reservation", metadata,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("operation_id", UUID(as_uuid=True), nullable=False),
    Column("instrument_id", UUID(as_uuid=True), nullable=False),
    Column("reserved_at", TIMESTAMP(timezone=True), nullable=False),
    Column("released_at", TIMESTAMP(timezone=True)),
    Column("active", Boolean, nullable=False),
)

instrument_cycle_event = Table(
    "instrument_cycle_event", metadata,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("instrument_id", UUID(as_uuid=True), nullable=False),
    Column("cycle_status_id", UUID(as_uuid=True), nullable=False),
    Column("occurred_at", TIMESTAMP(timezone=True), nullable=False),
    Column("session_id", UUID(as_uuid=True)),
    Column("operation_id", UUID(as_uuid=True)),
    Column("notes", Text),
)

cat_instrument_cycle_status = Table(
    "cat_instrument_cycle_status", metadata,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("code", String(32), nullable=False),
)

cat_operation_phase = Table(
    "cat_operation_phase", metadata,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("code", String(32), nullable=False),
    Column("active", Boolean, nullable=False),
)

privacy_notice_version = Table(
    "privacy_notice_version", metadata,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("effective_at", TIMESTAMP(timezone=True), nullable=False),
)

session_processing_agreement = Table(
    "session_processing_agreement", metadata,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("session_id", UUID(as_uuid=True), nullable=False),
    Column("privacy_notice_version_id", UUID(as_uuid=True), nullable=False),
    Column("purpose_quality_ops", Boolean, nullable=False),
)

# --- DS07: access_audit (transitional writer: adapters/audit_adapter.py only) ---------------

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
