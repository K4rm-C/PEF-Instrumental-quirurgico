from extensions import db
from sqlalchemy.dialects.postgresql import UUID, TIMESTAMP
from sqlalchemy.orm import Mapped,mapped_column,relationship
from sqlalchemy import ForeignKey, String, Text, Boolean
import uuid
from datetime import datetime, timezone

class Operation(db.Model):
    __tablename__ = 'operation'
    
    # Attributes
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    scheduled_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    started_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    ended_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    status_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('cat_operation_status.id'), nullable=False)
    procedure_type_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('cat_procedure_type.id'), nullable=False)
    room_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('operating_room.id'), nullable=False)
    institution_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('institution.id'), nullable=False)
    
    # Relations
    status: Mapped["CatOperationStatus"] = relationship("CatOperationStatus", back_populates="operations") # Operation -> CatOperationStatus 'operations'
    procedure_type: Mapped["CatProcedureType"] = relationship("CatProcedureType", back_populates="operations") # Operation -> CatProcedureType 'procedure_type'
    location: Mapped["OperatingRoom"] = relationship("OperatingRoom", back_populates="operations") # Operation -> OperatingRoom 'location'
    # Operation -> Institution 'institution'
    # Operation << OperationPhysichian 'team'
    cycle: Mapped[list["InstrumentCycleEvent"]] = relationship("InstrumentCycleEvent", back_populates="operation") # Operation << InstrumentCycleEvent 'cycle'
    involves: Mapped[list["OperationPatient"]] = relationship("OperationPatient", back_populates="operation") # Operation << OperationPatient 'involves'
    # Operation << InstrumentResevation 'reserves'
    sessions: Mapped[list["WorkSession"]] = relationship("WorkSession", back_populates="operation") # Operation << WorkSession 'sessions'