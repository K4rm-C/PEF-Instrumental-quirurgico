from extensions import db
from sqlalchemy.dialects.postgresql import UUID, TIMESTAMP
from sqlalchemy.orm import Mapped,mapped_column,relationship
from sqlalchemy import ForeignKey, Boolean
import uuid
from datetime import datetime, timezone

class WorkSession(db.Model):
    __tablename__ = 'work_session'
    
    # Attributes
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    started_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    ended_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    status_id: Mapped[uuid.UUID]  = mapped_column(ForeignKey('cat_session_status.id'), nullable=False)
    user_id: Mapped[uuid.UUID]  = mapped_column(ForeignKey('user.id'), nullable=False)
    operation_id: Mapped[uuid.UUID]  = mapped_column(ForeignKey('operation.id'), nullable=False)
    station_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('capture_station.id'), nullable=False)
    kit_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('kit.id'), nullable=False)
    current_phase_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey('cat_operation_phase.id'), nullable=True)
    atypical_session: Mapped[bool] = mapped_column(Boolean, nullable=False)
    extended_retention: Mapped[bool] = mapped_column(Boolean, nullable=False)
    retention_until: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    
    # Relations
    status: Mapped["CatSessionStatus"] = relationship("CatSessionStatus", back_populates="sessions") # WorkSession -> CatSessionStatus "status"
    # WorkSession -> User  "user"
    operation: Mapped["Operation"] = relationship("Operation", back_populates="sessions") # WorkSession -> Operation "operation"
    station: Mapped["CaptureStation"] = relationship("CaptureStation", back_populates="sessions") # WorkSession -> CaptureStation "station"
    kit: Mapped["Kit"] = relationship("Kit", back_populates="sessions") # WorkSession -> Kit "kit"
    audit: Mapped[list["CountEvent"]] = relationship("CountEvent", back_populates="session") # WorkSession << CountEvent "audit"
    conflicts: Mapped[list["Discrepancy"]] = relationship("Discrepancy", back_populates="session") # WorkSession << Discrepancy "conflicts"
    expected_inventory: Mapped[list["ExpectedInventory"]] = relationship("ExpectedInventory", back_populates="session") # WorkSession << ExpectedInventory "inventorySnapshot"
    cycle: Mapped[list["InstrumentCycleEvent"]] = relationship("InstrumentCycleEvent", back_populates="session") # WorkSession << InstrumentCycleEvent 'cycle'
    processing_agreement: Mapped["SessionProcessingAgreement | None"] = relationship("SessionProcessingAgreement", back_populates="session", uselist=False)
    current_phase: Mapped["CatOperationPhase | None"] = relationship("CatOperationPhase", back_populates="work_sessions")