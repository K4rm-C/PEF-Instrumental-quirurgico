from extensions import db
from sqlalchemy.dialects.postgresql import UUID, TIMESTAMP, JSONB
from sqlalchemy.orm import Mapped,mapped_column,relationship
from sqlalchemy import ForeignKey, String, Text, Boolean
import uuid
from datetime import datetime, timezone

class CatInstrumentCycleStatus(db.Model): # Missing String size
    __tablename__ = 'cat_instrument_cycle_status'
    
    # Attributes
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    code: Mapped[str] = mapped_column(String(), nullable=False)
    name: Mapped[str] = mapped_column(String(), nullable=False)
    
    # Relations
    instruments: Mapped[list["Instrument"]] = relationship("Instrument", back_populates="cycle_status") # CatInstrumentCycleStatus << Instrument 'instruments'
    instrument_cycle_events: Mapped[list["InstrumentCycleEvent"]] = relationship("InstrumentCycleEvent", back_populates="cycle_status") # CatInstrumentCycleStatus << InstrumentCycleEvent 'instrument_cycle_events'