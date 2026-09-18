from extensions import db
from sqlalchemy.dialects.postgresql import UUID, TIMESTAMP
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy import ForeignKey, Boolean
import uuid
from datetime import datetime, timezone

class InstrumentReservation(db.Model):
    __tablename__ = 'instrument_reservation'

    # Attributes
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    
    # Timestamps
    reserved_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    released_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=True) # Nullable porque aún no se ha liberado
    
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    # FKs
    operation_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('operation.id'), nullable=False)
    instrument_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('instrument.id'), nullable=False)

    # Relations
    operation: Mapped["Operation"] = relationship("Operation", back_populates="instrument_reservations")
    instrument: Mapped["Instrument"] = relationship("Instrument", back_populates="reservations")