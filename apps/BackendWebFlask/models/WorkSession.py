from extensions import db
from sqlalchemy.dialects.postgresql import UUID, TIMESTAMP
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy import ForeignKey, Boolean, String
import uuid
from datetime import datetime, timezone


class WorkSession(db.Model):
    __tablename__ = 'work_session'

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    started_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    ended_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    status_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('cat_session_status.id'), nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('user.id'), nullable=False)
    closed_by_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey('user.id'), nullable=True)
    operation_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey('operation.id'), nullable=True)
    station_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey('capture_station.id'), nullable=True)
    kit_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey('kit.id'), nullable=True)
    current_phase_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey('cat_operation_phase.id'), nullable=True)
    phase_changed_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    capture_mode: Mapped[str | None] = mapped_column(String(32), nullable=True)
    atypical_session: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    extended_retention: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    retention_until: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    status: Mapped["CatSessionStatus"] = relationship("CatSessionStatus", back_populates="sessions")
    operation: Mapped["Operation | None"] = relationship("Operation", back_populates="sessions")
    station: Mapped["CaptureStation | None"] = relationship("CaptureStation", back_populates="sessions")
    kit: Mapped["Kit | None"] = relationship("Kit", back_populates="sessions")
    audit: Mapped[list["CountEvent"]] = relationship("CountEvent", back_populates="session")
    conflicts: Mapped[list["Discrepancy"]] = relationship("Discrepancy", back_populates="session")
    expected_inventory: Mapped[list["ExpectedInventory"]] = relationship(
        "ExpectedInventory", back_populates="session"
    )
    cycle: Mapped[list["InstrumentCycleEvent"]] = relationship(
        "InstrumentCycleEvent", back_populates="session"
    )
    processing_agreement: Mapped["SessionProcessingAgreement | None"] = relationship(
        "SessionProcessingAgreement", back_populates="session", uselist=False
    )
    current_phase: Mapped["CatOperationPhase | None"] = relationship(
        "CatOperationPhase", back_populates="work_sessions"
    )
