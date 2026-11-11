import uuid
from datetime import datetime

from sqlalchemy import BigInteger, Boolean, CHAR, ForeignKey, Numeric, SmallInteger, String, Text
from sqlalchemy.dialects.postgresql import INET, JSONB, TIMESTAMP, UUID
from sqlalchemy.orm import Mapped, mapped_column

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Isolated SQLAlchemy mappings for Admin business logic (same PostgreSQL tables)."""
    pass


def _pk():
    return mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)


def _fk(target, nullable=False):
    return mapped_column(UUID(as_uuid=True), ForeignKey(target), nullable=nullable)


def _ts(nullable=False):
    return mapped_column(TIMESTAMP(timezone=True), nullable=nullable)


# --- Catalogs -----------------------------------------------------------------------------

class CatInstrumentCategory(Base):
    __tablename__ = 'cat_instrument_category'
    id: Mapped[uuid.UUID] = _pk()
    code: Mapped[str] = mapped_column(String(64))
    name: Mapped[str] = mapped_column(String(120))


class CatInstrumentCycleStatus(Base):
    __tablename__ = 'cat_instrument_cycle_status'
    id: Mapped[uuid.UUID] = _pk()
    code: Mapped[str] = mapped_column(String(64))
    name: Mapped[str] = mapped_column(String(120))


class CatOperationPhase(Base):
    __tablename__ = 'cat_operation_phase'
    id: Mapped[uuid.UUID] = _pk()
    code: Mapped[str] = mapped_column(String(32))
    name: Mapped[str] = mapped_column(String(80))
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class CatProcedureType(Base):
    __tablename__ = 'cat_procedure_type'
    id: Mapped[uuid.UUID] = _pk()
    code: Mapped[str] = mapped_column(String(64))
    name: Mapped[str] = mapped_column(String(200))


class CatDiscrepancyReason(Base):
    __tablename__ = 'cat_discrepancy_reason'
    id: Mapped[uuid.UUID] = _pk()
    code: Mapped[str] = mapped_column(String(64))
    name: Mapped[str] = mapped_column(String(160))


# --- Institution and identity -------------------------------------------------------------

class Institution(Base):
    __tablename__ = 'institution'
    id: Mapped[uuid.UUID] = _pk()
    name: Mapped[str] = mapped_column(String(200))
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    parent_id: Mapped[uuid.UUID | None] = _fk('institution.id', nullable=True)


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
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    email: Mapped[str] = mapped_column(String(255))
    password_hash: Mapped[str] = mapped_column(Text)
    password_updated_at: Mapped[datetime | None] = _ts(nullable=True)
    last_login_at: Mapped[datetime | None] = _ts(nullable=True)
    created_at: Mapped[datetime] = _ts()
    updated_at: Mapped[datetime] = _ts()
    institution_id: Mapped[uuid.UUID] = _fk('institution.id')


class UserRole(Base):
    __tablename__ = 'user_role'
    id: Mapped[uuid.UUID] = _pk()
    assigned_at: Mapped[datetime] = _ts()
    user_id: Mapped[uuid.UUID] = _fk('user.id')
    role_id: Mapped[uuid.UUID] = _fk('role.id')


# --- Locations ------------------------------------------------------------------------------

class UserOperatingRoom(Base):
    """Floor supervisor (or_charge) <-> operating room (migration 008)."""
    __tablename__ = 'user_operating_room'
    id: Mapped[uuid.UUID] = _pk()
    user_id: Mapped[uuid.UUID] = _fk('user.id')
    room_id: Mapped[uuid.UUID] = _fk('operating_room.id')
    assigned_at: Mapped[datetime] = _ts()


class AppSetting(Base):
    """RF-AD-10 application parameter of one institution (migration 010)."""
    __tablename__ = 'app_setting'
    institution_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey('institution.id'), primary_key=True)
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[object] = mapped_column(JSONB)
    updated_at: Mapped[datetime] = _ts()
    updated_by_user_id: Mapped[uuid.UUID | None] = _fk('user.id', nullable=True)


class OperatingRoom(Base):
    __tablename__ = 'operating_room'
    id: Mapped[uuid.UUID] = _pk()
    code: Mapped[str] = mapped_column(String(32))
    name: Mapped[str] = mapped_column(String(120))
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    institution_id: Mapped[uuid.UUID] = _fk('institution.id')


class CaptureStation(Base):
    __tablename__ = 'capture_station'
    id: Mapped[uuid.UUID] = _pk()
    name: Mapped[str] = mapped_column(String(120))
    roi: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    room_id: Mapped[uuid.UUID] = _fk('operating_room.id')


class CaptureCamera(Base):
    """MS-05 camera of a capture station (migration 012)."""
    __tablename__ = 'capture_camera'
    id: Mapped[uuid.UUID] = _pk()
    institution_id: Mapped[uuid.UUID] = _fk('institution.id')
    station_id: Mapped[uuid.UUID] = _fk('capture_station.id')
    name: Mapped[str] = mapped_column(String(120))
    device_type: Mapped[str] = mapped_column(String(20), default='usb')
    resolution_width: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    resolution_height: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    fps: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    roi: Mapped[dict | None] = mapped_column(JSONB(none_as_null=True), nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), default=datetime.utcnow)


class CaptureCameraCalibration(Base):
    """MS-12 calibration MEASURED by a technician (migration 019): known reference length in the image -> mm/pixel.
    Immutable history; the latest row is the current calibration."""
    __tablename__ = 'capture_camera_calibration'
    id: Mapped[uuid.UUID] = _pk()
    institution_id: Mapped[uuid.UUID] = _fk('institution.id')
    camera_id: Mapped[uuid.UUID] = _fk('capture_camera.id')
    method: Mapped[str] = mapped_column(String(30), default='reference_length')
    reference_length_mm = mapped_column(Numeric(9, 3))
    measured_pixels = mapped_column(Numeric(9, 3))
    mm_per_pixel = mapped_column(Numeric(12, 6))
    frame_width: Mapped[int] = mapped_column(SmallInteger)
    frame_height: Mapped[int] = mapped_column(SmallInteger)
    notes: Mapped[str | None] = mapped_column(String(300), nullable=True)
    performed_by_user_id: Mapped[uuid.UUID] = _fk('user.id')
    performed_at: Mapped[datetime] = _ts()


class MediaAsset(Base):
    __tablename__ = 'media_asset'
    id: Mapped[uuid.UUID] = _pk()
    gcs_uri: Mapped[str] = mapped_column(Text)
    bucket: Mapped[str] = mapped_column(String(128))
    object_key: Mapped[str] = mapped_column(Text)
    content_type: Mapped[str | None] = mapped_column(String(128), nullable=True)
    kind: Mapped[str] = mapped_column(String(32))
    storage_provider: Mapped[str] = mapped_column(String(32))
    sha256: Mapped[str | None] = mapped_column(CHAR(64), nullable=True)
    size_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    created_at: Mapped[datetime] = _ts()
    updated_at: Mapped[datetime] = _ts()


class PrivacyNoticeVersion(Base):
    """RF-AD-04 library of privacy notices (the Supervisor links one to a session, RF-SP-02)."""
    __tablename__ = 'privacy_notice_version'
    id: Mapped[uuid.UUID] = _pk()
    version: Mapped[str] = mapped_column(String(32))
    effective_at: Mapped[datetime] = _ts()
    document_uri: Mapped[str] = mapped_column(Text)
    content_sha256: Mapped[str | None] = mapped_column(CHAR(64), nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = _ts()


# --- Instruments, kits, procedures and models ------------------------------------------------

class InstrumentFamily(Base):
    __tablename__ = 'instrument_family'
    id: Mapped[uuid.UUID] = _pk()
    code: Mapped[str] = mapped_column(String(64))
    name: Mapped[str] = mapped_column(String(160))
    category_id: Mapped[uuid.UUID] = _fk('cat_instrument_category.id')
    identify_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    classify_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    function_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class FamilyExample(Base):
    """RF-AD-02 teaching photo of a family (image in object storage via media_asset)."""
    __tablename__ = 'family_example'
    id: Mapped[uuid.UUID] = _pk()
    family_id: Mapped[uuid.UUID] = _fk('instrument_family.id')
    media_asset_id: Mapped[uuid.UUID | None] = _fk('media_asset.id', nullable=True)
    example_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    sort_order: Mapped[int] = mapped_column(SmallInteger, default=0)


class Instrument(Base):
    __tablename__ = 'instrument'
    id: Mapped[uuid.UUID] = _pk()
    internal_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    family_id: Mapped[uuid.UUID] = _fk('instrument_family.id')
    cycle_status_id: Mapped[uuid.UUID] = _fk('cat_instrument_cycle_status.id')
    institution_id: Mapped[uuid.UUID] = _fk('institution.id')
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = _ts()
    updated_at: Mapped[datetime] = _ts()


class InstrumentCycleEvent(Base):
    __tablename__ = 'instrument_cycle_event'
    id: Mapped[uuid.UUID] = _pk()
    instrument_id: Mapped[uuid.UUID] = _fk('instrument.id')
    cycle_status_id: Mapped[uuid.UUID] = _fk('cat_instrument_cycle_status.id')
    occurred_at: Mapped[datetime] = _ts()
    session_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    operation_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)


class Kit(Base):
    __tablename__ = 'kit'
    id: Mapped[uuid.UUID] = _pk()
    name: Mapped[str] = mapped_column(String(160))
    version: Mapped[int] = mapped_column(SmallInteger, default=1)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
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
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = _ts()
    updated_at: Mapped[datetime] = _ts()


class ProcedurePhase(Base):
    __tablename__ = 'procedure_phase'
    id: Mapped[uuid.UUID] = _pk()
    procedure_type_id: Mapped[uuid.UUID] = _fk('cat_procedure_type.id')
    phase_id: Mapped[uuid.UUID] = _fk('cat_operation_phase.id')
    sort_order: Mapped[int] = mapped_column(SmallInteger)
    is_count_required: Mapped[bool] = mapped_column(Boolean, default=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class YoloModel(Base):
    __tablename__ = 'yolo_model'
    id: Mapped[uuid.UUID] = _pk()
    version_tag: Mapped[str] = mapped_column(String(32))
    media_asset_id: Mapped[uuid.UUID | None] = _fk('media_asset.id', nullable=True)
    checksum: Mapped[str | None] = mapped_column(CHAR(64), nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=False)
    published_at: Mapped[datetime] = _ts()
    architecture: Mapped[str | None] = mapped_column(String(64), nullable=True)
    model_size: Mapped[str | None] = mapped_column(String(8), nullable=True)
    class_names: Mapped[dict | None] = mapped_column(JSONB, nullable=True)


class ModelClass(Base):
    __tablename__ = 'model_class'
    id: Mapped[uuid.UUID] = _pk()
    model_id: Mapped[uuid.UUID] = _fk('yolo_model.id')
    family_id: Mapped[uuid.UUID] = _fk('instrument_family.id')
    yolo_class_id: Mapped[int] = mapped_column(SmallInteger)


# --- Audit ----------------------------------------------------------------------------------

class AccessAudit(Base):
    __tablename__ = 'access_audit'
    id: Mapped[uuid.UUID] = _pk()
    occurred_at: Mapped[datetime] = _ts()
    actor_type: Mapped[str] = mapped_column(String(16))
    actor_user_id: Mapped[uuid.UUID | None] = _fk('user.id', nullable=True)
    actor_client_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    action: Mapped[str] = mapped_column(String(64))
    resource_type: Mapped[str] = mapped_column(String(64))
    resource_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    institution_id: Mapped[uuid.UUID | None] = _fk('institution.id', nullable=True)
    outcome: Mapped[str] = mapped_column(String(16), default='success')
    correlation_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    ip: Mapped[str | None] = mapped_column(INET, nullable=True)
