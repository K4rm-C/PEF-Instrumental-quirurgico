from extensions import db
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy import String, UniqueConstraint, text
import uuid


class CatInstrumentCycleStatus(db.Model):
    __tablename__ = 'cat_instrument_cycle_status'
    __table_args__ = (
        UniqueConstraint('code', name='uk_cat_instrument_cycle_status_code'),
    )

    # Attributes
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, server_default=text('gen_random_uuid()'))
    code: Mapped[str] = mapped_column(String(32), nullable=False)
    name: Mapped[str] = mapped_column(String(80), nullable=False)

    # Relations
    instruments: Mapped[list["Instrument"]] = relationship("Instrument", back_populates="cycle_status") # CatInstrumentCycleStatus << Instrument 'instruments'
    instrument_cycle_events: Mapped[list["InstrumentCycleEvent"]] = relationship("InstrumentCycleEvent", back_populates="cycle_status") # CatInstrumentCycleStatus << InstrumentCycleEvent 'instrument_cycle_events'
