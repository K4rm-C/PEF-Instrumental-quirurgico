from extensions import db
from sqlalchemy.dialects.postgresql import UUID, TIMESTAMP
from sqlalchemy.orm import Mapped,mapped_column,relationship
from sqlalchemy import ForeignKey, CheckConstraint, text
import uuid
from datetime import datetime

class Operation(db.Model):
    __tablename__ = 'operation'
    __table_args__ = (
        CheckConstraint(
            'ended_at IS NULL OR started_at IS NULL OR ended_at >= started_at',
            name='chk_operation_timeline',
        ),
    )

    # Attributes
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, server_default=text('gen_random_uuid()'))
    scheduled_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    ended_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    status_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('cat_operation_status.id', ondelete='RESTRICT'), nullable=False)
    procedure_type_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey('cat_procedure_type.id', ondelete='SET NULL'), nullable=True)
    room_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey('operating_room.id', ondelete='SET NULL'), nullable=True)
    institution_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('institution.id', ondelete='RESTRICT'), nullable=False)

    # Relations
    status: Mapped["CatOperationStatus"] = relationship("CatOperationStatus", back_populates="operations") # Operation -> CatOperationStatus 'operations'
    procedure_type: Mapped["CatProcedureType | None"] = relationship("CatProcedureType", back_populates="operations") # Operation -> CatProcedureType 'procedure_type'
    location: Mapped["OperatingRoom | None"] = relationship("OperatingRoom", back_populates="operations") # Operation -> OperatingRoom 'location'
    # Operation -> Institution 'institution'
    operation_physicians: Mapped[list["OperationPhysician"]] = relationship("OperationPhysician", back_populates="operation") # Operation << OperationPhysichian 'operation_physicians'
    cycle: Mapped[list["InstrumentCycleEvent"]] = relationship("InstrumentCycleEvent", back_populates="operation") # Operation << InstrumentCycleEvent 'cycle'
    involves: Mapped[list["OperationPatient"]] = relationship("OperationPatient", back_populates="operation") # Operation << OperationPatient 'involves'
    instrument_reservations: Mapped[list["InstrumentReservation"]] = relationship("InstrumentReservation", back_populates="operation") # Operation << InstrumentResevation 'instrument_reservation'
    sessions: Mapped[list["WorkSession"]] = relationship("WorkSession", back_populates="operation") # Operation << WorkSession 'sessions'
