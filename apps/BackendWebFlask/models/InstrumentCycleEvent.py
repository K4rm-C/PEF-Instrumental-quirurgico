from extensions import db
from sqlalchemy.dialects.postgresql import UUID, TIMESTAMP
from sqlalchemy.orm import Mapped,mapped_column,relationship
from sqlalchemy import ForeignKey, Text, func, text
import uuid
from datetime import datetime

class InstrumentCycleEvent(db.Model):
    __tablename__ = 'instrument_cycle_event'

    # Attributes
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, server_default=text('gen_random_uuid()'))
    instrument_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('instrument.id', ondelete='CASCADE'), nullable=False)
    cycle_status_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('cat_instrument_cycle_status.id', ondelete='RESTRICT'), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False, server_default=func.now())
    # In 001_init.sql this FK is added later via ALTER TABLE (section 5.8)
    session_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey('work_session.id', ondelete='SET NULL'), nullable=True)
    operation_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey('operation.id', ondelete='SET NULL'), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Relations
    instrument: Mapped["Instrument"] = relationship("Instrument", back_populates="history") # InstrumentCycleEvent -> Instrument 'instrument'
    cycle_status: Mapped["CatInstrumentCycleStatus"] = relationship("CatInstrumentCycleStatus", back_populates="instrument_cycle_events") # InstrumentCycleEvent -> CatInstrumentCycleStatus 'cycle_status'
    session: Mapped["WorkSession | None"] = relationship("WorkSession", back_populates="cycle") # InstrumentCycleEvent -> WorkSession 'session'
    operation: Mapped["Operation | None"] = relationship("Operation", back_populates="cycle") # InstrumentCycleEvent -> Operation 'operation'
