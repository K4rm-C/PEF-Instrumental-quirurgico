from extensions import db
from sqlalchemy.dialects.postgresql import UUID, TIMESTAMP, JSONB
from sqlalchemy.orm import Mapped,mapped_column,relationship
from sqlalchemy import ForeignKey, String, Text, Boolean
import uuid
from datetime import datetime, timezone

class Instrument(db.Model): # Missing String size
    __tablename__ = 'instrument'
    
    # Attributes
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    internal_code: Mapped[str] = mapped_column(String(), nullable=False)
    family_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('instrument_family.id'), nullable=False)
    cycle_status_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('cat_instrument_cycle_status.id'), nullable=False)
    institution_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('institution.id'), nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False)
    
    # Relations
    family: Mapped["InstrumentFamily"] = relationship("InstrumentFamily") # Instrument -> InstrumentFamily 'family'
    cycle_status: Mapped["CatInstrumentCycleStatus"] = relationship("CatInstrumentCycleStatus", back_populates="instruments") # Instrument -> CatInstrumentCycleStatus 'cycle_status'
    institution: Mapped["Institution"] = relationship("Institution", back_populates="instruments") # Instrument -> Institution 'institution'
    history: Mapped[list["InstrumentCycleEvent"]] = relationship("InstrumentCycleEvent", back_populates="instrument") # Instrument << InstrumentCycleEvent 'history'
    reservations: Mapped[list["InstrumentReservation"]] = relationship("InstrumentReservation", back_populates="instrument") # Instrument << InstrumentReservation 'reservations'
    usages: Mapped[list["InstrumentUsage"]] = relationship("InstrumentUsage", back_populates="instrument") # Instrument << InstrumentUsage 'usages'