import uuid
from datetime import datetime

from sqlalchemy import Boolean, ForeignKey, SmallInteger, String, Text
from sqlalchemy.dialects.postgresql import INET, JSONB, TIMESTAMP, UUID
from sqlalchemy.orm import Mapped, mapped_column

from database import Base


def _pk():
    return mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)


def _fk(target, nullable=False):
    return mapped_column(UUID(as_uuid=True), ForeignKey(target), nullable=nullable)


def _ts(nullable=False):
    return mapped_column(TIMESTAMP(timezone=True), nullable=nullable)


class Institution(Base):
    __tablename__ = 'institution'
    id: Mapped[uuid.UUID] = _pk()
    name: Mapped[str] = mapped_column(String(200))
    active: Mapped[bool] = mapped_column(Boolean)


class Role(Base):
    __tablename__ = 'role'
    id: Mapped[uuid.UUID] = _pk()
    code: Mapped[str] = mapped_column(String(64))
    description: Mapped[str] = mapped_column(String(255))
    institution_id: Mapped[uuid.UUID] = _fk('institution.id')


class User(Base):
    __tablename__ = 'user'
    id: Mapped[uuid.UUID] = _pk()
    name: Mapped[str] = mapped_column(String(160))
    active: Mapped[bool] = mapped_column(Boolean)
    email: Mapped[str] = mapped_column(String(255))
    institution_id: Mapped[uuid.UUID] = _fk('institution.id')


class UserRole(Base):
    __tablename__ = 'user_role'
    id: Mapped[uuid.UUID] = _pk()
    user_id: Mapped[uuid.UUID] = _fk('user.id')
    role_id: Mapped[uuid.UUID] = _fk('role.id')


class CatSessionStatus(Base):
    __tablename__ = 'cat_session_status'
    id: Mapped[uuid.UUID] = _pk()
    code: Mapped[str] = mapped_column(String(32))
    name: Mapped[str] = mapped_column(String(80))


class CatOperationStatus(Base):
    __tablename__ = 'cat_operation_status'
    id: Mapped[uuid.UUID] = _pk()
    code: Mapped[str] = mapped_column(String(32))


class CatOperationPhase(Base):
    __tablename__ = 'cat_operation_phase'
    id: Mapped[uuid.UUID] = _pk()
    code: Mapped[str] = mapped_column(String(32))
    name: Mapped[str] = mapped_column(String(80))
    active: Mapped[bool] = mapped_column(Boolean)


class CatProcedureType(Base):
    __tablename__ = 'cat_procedure_type'
    id: Mapped[uuid.UUID] = _pk()
    code: Mapped[str] = mapped_column(String(64))
    name: Mapped[str] = mapped_column(String(200))


class ProcedurePhase(Base):
    __tablename__ = 'procedure_phase'
    id: Mapped[uuid.UUID] = _pk()
    procedure_type_id: Mapped[uuid.UUID] = _fk('cat_procedure_type.id')
    phase_id: Mapped[uuid.UUID] = _fk('cat_operation_phase.id')
    sort_order: Mapped[int] = mapped_column(SmallInteger)
    active: Mapped[bool] = mapped_column(Boolean)


class CatSurgicalRole(Base):
    __tablename__ = 'cat_surgical_role'
    id: Mapped[uuid.UUID] = _pk()
    code: Mapped[str] = mapped_column(String(64))
    name: Mapped[str] = mapped_column(String(120))


class CatInstrumentCycleStatus(Base):
    __tablename__ = 'cat_instrument_cycle_status'
    id: Mapped[uuid.UUID] = _pk()
    code: Mapped[str] = mapped_column(String(32))
    name: Mapped[str] = mapped_column(String(80))


class CatInstrumentCategory(Base):
    __tablename__ = 'cat_instrument_category'
    id: Mapped[uuid.UUID] = _pk()
    code: Mapped[str] = mapped_column(String(64))
    name: Mapped[str] = mapped_column(String(120))


class CatEventType(Base):
    __tablename__ = 'cat_event_type'
    id: Mapped[uuid.UUID] = _pk()
    code: Mapped[str] = mapped_column(String(64))


class CatDiscrepancyReason(Base):
    __tablename__ = 'cat_discrepancy_reason'
    id: Mapped[uuid.UUID] = _pk()
    code: Mapped[str] = mapped_column(String(64))
    name: Mapped[str] = mapped_column(String(160))


class OperatingRoom(Base):
    __tablename__ = 'operating_room'
    id: Mapped[uuid.UUID] = _pk()
    code: Mapped[str] = mapped_column(String(32))
    name: Mapped[str] = mapped_column(String(120))
    active: Mapped[bool] = mapped_column(Boolean)
    institution_id: Mapped[uuid.UUID] = _fk('institution.id')


class CaptureStation(Base):
    __tablename__ = 'capture_station'
    id: Mapped[uuid.UUID] = _pk()
    name: Mapped[str] = mapped_column(String(120))
    active: Mapped[bool] = mapped_column(Boolean)
    room_id: Mapped[uuid.UUID] = _fk('operating_room.id')


class Patient(Base):
    __tablename__ = 'patient'
    id: Mapped[uuid.UUID] = _pk()
    display_name: Mapped[str] = mapped_column(String(160))
    active: Mapped[bool] = mapped_column(Boolean)
    institution_id: Mapped[uuid.UUID] = _fk('institution.id')


class Physician(Base):
    __tablename__ = 'physician'
    id: Mapped[uuid.UUID] = _pk()
    name: Mapped[str] = mapped_column(String(160))
    active: Mapped[bool] = mapped_column(Boolean)
    institution_id: Mapped[uuid.UUID] = _fk('institution.id')


class Operation(Base):
    __tablename__ = 'operation'
    id: Mapped[uuid.UUID] = _pk()
    scheduled_at: Mapped[datetime | None] = _ts(nullable=True)
    started_at: Mapped[datetime | None] = _ts(nullable=True)
    ended_at: Mapped[datetime | None] = _ts(nullable=True)
    status_id: Mapped[uuid.UUID] = _fk('cat_operation_status.id')
    procedure_type_id: Mapped[uuid.UUID | None] = _fk('cat_procedure_type.id', nullable=True)
    room_id: Mapped[uuid.UUID | None] = _fk('operating_room.id', nullable=True)
    institution_id: Mapped[uuid.UUID] = _fk('institution.id')


class OperationPatient(Base):
    __tablename__ = 'operation_patient'
    id: Mapped[uuid.UUID] = _pk()
    operation_id: Mapped[uuid.UUID] = _fk('operation.id')
    patient_id: Mapped[uuid.UUID] = _fk('patient.id')


class OperationPhysician(Base):
    __tablename__ = 'operation_physician'
    id: Mapped[uuid.UUID] = _pk()
    operation_id: Mapped[uuid.UUID] = _fk('operation.id')
    physician_id: Mapped[uuid.UUID] = _fk('physician.id')
    surgical_role_id: Mapped[uuid.UUID] = _fk('cat_surgical_role.id')


class InstrumentFamily(Base):
    __tablename__ = 'instrument_family'
    id: Mapped[uuid.UUID] = _pk()
    code: Mapped[str] = mapped_column(String(64))
    name: Mapped[str] = mapped_column(String(160))
    category_id: Mapped[uuid.UUID] = _fk('cat_instrument_category.id')
    active: Mapped[bool] = mapped_column(Boolean)


class Instrument(Base):
    __tablename__ = 'instrument'
    id: Mapped[uuid.UUID] = _pk()
    internal_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    family_id: Mapped[uuid.UUID] = _fk('instrument_family.id')
    cycle_status_id: Mapped[uuid.UUID] = _fk('cat_instrument_cycle_status.id')
    institution_id: Mapped[uuid.UUID] = _fk('institution.id')
    active: Mapped[bool] = mapped_column(Boolean)
    created_at: Mapped[datetime] = _ts()
    updated_at: Mapped[datetime] = _ts()


class InstrumentCycleEvent(Base):
    __tablename__ = 'instrument_cycle_event'
    id: Mapped[uuid.UUID] = _pk()
    instrument_id: Mapped[uuid.UUID] = _fk('instrument.id')
    cycle_status_id: Mapped[uuid.UUID] = _fk('cat_instrument_cycle_status.id')
    occurred_at: Mapped[datetime] = _ts()
    session_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    operation_id: Mapped[uuid.UUID | None] = _fk('operation.id', nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)


class InstrumentReservation(Base):
    __tablename__ = 'instrument_reservation'
    id: Mapped[uuid.UUID] = _pk()
    operation_id: Mapped[uuid.UUID] = _fk('operation.id')
    instrument_id: Mapped[uuid.UUID] = _fk('instrument.id')
    reserved_at: Mapped[datetime] = _ts()
    released_at: Mapped[datetime | None] = _ts(nullable=True)
    active: Mapped[bool] = mapped_column(Boolean)


class Kit(Base):
    __tablename__ = 'kit'
    id: Mapped[uuid.UUID] = _pk()
    name: Mapped[str] = mapped_column(String(160))
    version: Mapped[int] = mapped_column(SmallInteger)
    active: Mapped[bool] = mapped_column(Boolean)
    institution_id: Mapped[uuid.UUID] = _fk('institution.id')
    created_at: Mapped[datetime] = _ts()
    updated_at: Mapped[datetime] = _ts()


class KitItem(Base):
    __tablename__ = 'kit_item'
    id: Mapped[uuid.UUID] = _pk()
    kit_id: Mapped[uuid.UUID] = _fk('kit.id')
    family_id: Mapped[uuid.UUID] = _fk('instrument_family.id')
    quantity: Mapped[int] = mapped_column(SmallInteger)


class ProcedureKit(Base):
    __tablename__ = 'procedure_kit'
    id: Mapped[uuid.UUID] = _pk()
    procedure_type_id: Mapped[uuid.UUID] = _fk('cat_procedure_type.id')
    kit_id: Mapped[uuid.UUID] = _fk('kit.id')
    technique_label: Mapped[str | None] = mapped_column(String(160), nullable=True)
    is_default: Mapped[bool] = mapped_column(Boolean)
    active: Mapped[bool] = mapped_column(Boolean)
    created_at: Mapped[datetime] = _ts()
    updated_at: Mapped[datetime] = _ts()


class WorkSession(Base):
    __tablename__ = 'work_session'
    id: Mapped[uuid.UUID] = _pk()
    started_at: Mapped[datetime | None] = _ts(nullable=True)
    ended_at: Mapped[datetime | None] = _ts(nullable=True)
    status_id: Mapped[uuid.UUID] = _fk('cat_session_status.id')
    user_id: Mapped[uuid.UUID] = _fk('user.id')
    closed_by_user_id: Mapped[uuid.UUID | None] = _fk('user.id', nullable=True)
    operation_id: Mapped[uuid.UUID | None] = _fk('operation.id', nullable=True)
    station_id: Mapped[uuid.UUID | None] = _fk('capture_station.id', nullable=True)
    kit_id: Mapped[uuid.UUID | None] = _fk('kit.id', nullable=True)
    current_phase_id: Mapped[uuid.UUID | None] = _fk('cat_operation_phase.id', nullable=True)
    phase_changed_at: Mapped[datetime | None] = _ts(nullable=True)
    capture_mode: Mapped[str | None] = mapped_column(String(32), nullable=True)
    atypical_session: Mapped[bool] = mapped_column(Boolean)
    extended_retention: Mapped[bool] = mapped_column(Boolean)
    retention_until: Mapped[datetime | None] = _ts(nullable=True)
    updated_at: Mapped[datetime] = _ts()


class ExpectedInventory(Base):
    __tablename__ = 'expected_inventory'
    id: Mapped[uuid.UUID] = _pk()
    family_id: Mapped[uuid.UUID] = _fk('instrument_family.id')
    expected_quantity: Mapped[int] = mapped_column(SmallInteger)
    session_id: Mapped[uuid.UUID] = _fk('work_session.id')
    source: Mapped[str] = mapped_column(String(32))


class CountEvent(Base):
    __tablename__ = 'count_event'
    id: Mapped[uuid.UUID] = _pk()
    event_type_id: Mapped[uuid.UUID] = _fk('cat_event_type.id')
    client_event_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    family_id: Mapped[uuid.UUID | None] = _fk('instrument_family.id', nullable=True)
    expected_quantity: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    detected_quantity: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    payload: Mapped[dict] = mapped_column(JSONB, default=dict)
    occurred_at: Mapped[datetime] = _ts()
    session_id: Mapped[uuid.UUID] = _fk('work_session.id')
    user_id: Mapped[uuid.UUID | None] = _fk('user.id', nullable=True)


class Discrepancy(Base):
    __tablename__ = 'discrepancy'
    id: Mapped[uuid.UUID] = _pk()
    description: Mapped[str] = mapped_column(Text)
    resolved: Mapped[bool] = mapped_column(Boolean)
    resolved_at: Mapped[datetime | None] = _ts(nullable=True)
    reason_id: Mapped[uuid.UUID | None] = _fk('cat_discrepancy_reason.id', nullable=True)
    family_id: Mapped[uuid.UUID | None] = _fk('instrument_family.id', nullable=True)
    expected_quantity: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    detected_quantity: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    session_id: Mapped[uuid.UUID] = _fk('work_session.id')
    origin_event_id: Mapped[uuid.UUID | None] = _fk('count_event.id', nullable=True)
    updated_at: Mapped[datetime] = _ts()


class PrivacyNoticeVersion(Base):
    __tablename__ = 'privacy_notice_version'
    id: Mapped[uuid.UUID] = _pk()
    version: Mapped[str] = mapped_column(String(32))
    effective_at: Mapped[datetime] = _ts()
    document_uri: Mapped[str] = mapped_column(Text)
    active: Mapped[bool] = mapped_column(Boolean)


class SessionProcessingAgreement(Base):
    __tablename__ = 'session_processing_agreement'
    id: Mapped[uuid.UUID] = _pk()
    session_id: Mapped[uuid.UUID] = _fk('work_session.id')
    privacy_notice_version_id: Mapped[uuid.UUID] = _fk('privacy_notice_version.id')
    purpose_quality_ops: Mapped[bool] = mapped_column(Boolean)
    purpose_model_improvement: Mapped[bool] = mapped_column(Boolean)
    agreed_at: Mapped[datetime] = _ts()
    agreed_by_user_id: Mapped[uuid.UUID | None] = _fk('user.id', nullable=True)


class AccessAudit(Base):
    __tablename__ = 'access_audit'
    id: Mapped[uuid.UUID] = _pk()
    occurred_at: Mapped[datetime] = _ts()
    actor_type: Mapped[str] = mapped_column(String(16))
    actor_user_id: Mapped[uuid.UUID | None] = _fk('user.id', nullable=True)
    action: Mapped[str] = mapped_column(String(64))
    resource_type: Mapped[str] = mapped_column(String(64))
    resource_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    institution_id: Mapped[uuid.UUID | None] = _fk('institution.id', nullable=True)
    outcome: Mapped[str] = mapped_column(String(16))
    correlation_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    ip: Mapped[str | None] = mapped_column(INET, nullable=True)
