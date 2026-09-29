from extensions import db
from sqlalchemy.dialects.postgresql import UUID, TIMESTAMP
from sqlalchemy.orm import Mapped,mapped_column,relationship
from sqlalchemy import ForeignKey, Boolean, CheckConstraint, func, text
import uuid
from datetime import datetime

class WorkSession(db.Model):
    __tablename__ = 'work_session'
    __table_args__ = (
        CheckConstraint('ended_at IS NULL OR ended_at >= started_at', name='chk_work_session_ended'),
    )

    # Attributes
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, server_default=text('gen_random_uuid()'))
    started_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False, server_default=func.now())
    ended_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    status_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('cat_session_status.id', ondelete='RESTRICT'), nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('user.id', ondelete='RESTRICT'), nullable=False)
    closed_by_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey('user.id', ondelete='SET NULL'), nullable=True)
    operation_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey('operation.id', ondelete='RESTRICT'), nullable=True)
    station_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey('capture_station.id', ondelete='SET NULL'), nullable=True)
    kit_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey('kit.id', ondelete='SET NULL'), nullable=True)
    current_phase_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey('cat_operation_phase.id', ondelete='SET NULL'), nullable=True)
    phase_changed_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    atypical_session: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=text('false'))
    extended_retention: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=text('false'))
    retention_until: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())

    # Relations
    status: Mapped["CatSessionStatus"] = relationship("CatSessionStatus", back_populates="sessions") # WorkSession -> CatSessionStatus "status"
    # Two FKs point to user.id, so each relationship must name its own column
    user: Mapped["User"] = relationship("User", back_populates="work_sessions", foreign_keys=[user_id]) # WorkSession -> User "user" (opened the session)
    closed_by_user: Mapped["User | None"] = relationship("User", back_populates="closed_work_sessions", foreign_keys=[closed_by_user_id]) # WorkSession -> User "closed_by_user"
    operation: Mapped["Operation | None"] = relationship("Operation", back_populates="sessions") # WorkSession -> Operation "operation"
    station: Mapped["CaptureStation | None"] = relationship("CaptureStation", back_populates="sessions") # WorkSession -> CaptureStation "station"
    kit: Mapped["Kit | None"] = relationship("Kit", back_populates="sessions") # WorkSession -> Kit "kit"
    audit: Mapped[list["CountEvent"]] = relationship("CountEvent", back_populates="session") # WorkSession << CountEvent "audit"
    conflicts: Mapped[list["Discrepancy"]] = relationship("Discrepancy", back_populates="session") # WorkSession << Discrepancy "conflicts"
    expected_inventory: Mapped[list["ExpectedInventory"]] = relationship("ExpectedInventory", back_populates="session") # WorkSession << ExpectedInventory "inventorySnapshot"
    cycle: Mapped[list["InstrumentCycleEvent"]] = relationship("InstrumentCycleEvent", back_populates="session") # WorkSession << InstrumentCycleEvent 'cycle'
    processing_agreement: Mapped["SessionProcessingAgreement | None"] = relationship("SessionProcessingAgreement", back_populates="session", uselist=False)
    current_phase: Mapped["CatOperationPhase | None"] = relationship("CatOperationPhase", back_populates="work_sessions")
