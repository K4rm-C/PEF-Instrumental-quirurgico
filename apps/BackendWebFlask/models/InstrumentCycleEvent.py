from extensions import db
from sqlalchemy.dialects.postgresql import UUID, TIMESTAMP, JSONB
from sqlalchemy.orm import Mapped,mapped_column,relationship
from sqlalchemy import ForeignKey, String, Text, Boolean
import uuid
from datetime import datetime, timezone

class InstrumentCycleEvent(db.Model):
    __tablename__ = 'instrument_cycle_event'
    
    # Attributes
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    instrument_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('instrument.id'), nullable=False)
    cycle_status_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('cat_instrument_cycle_status.id'), nullable=False)
    ocurred_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    session_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('work_session.id'), nullable=False)
    operation_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('operation.id'), nullable=False)
    notes: Mapped[str] = mapped_column(Text, nullable=False)
    
    # Relations
    instrument: Mapped["Instrument"] = relationship("Instrument", back_populates="history") # InstrumentCycleEvent -> Instrument 'instrument'
    cycle_status: Mapped["CatInstrumentCycleStatus"] = relationship("CatInstrumentCycleStatus", back_populates="instrument_cycle_events") # InstrumentCycleEvent -> CatInstrumentCycleStatus 'cycle_status'
    session: Mapped["WorkSession"] = relationship("WorkSession", back_populates="cycle") # InstrumentCycleEvent -> WorkSession 'session'
    operation: Mapped["Operation"] = relationship("Operation", back_populates="cycle") # InstrumentCycleEvent -> Operation 'operation'