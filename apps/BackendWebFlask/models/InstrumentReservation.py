from extensions import db
from sqlalchemy.dialects.postgresql import UUID, TIMESTAMP
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy import ForeignKey, Boolean, CheckConstraint, Index, func, text
import uuid
from datetime import datetime

class InstrumentReservation(db.Model):
    __tablename__ = 'instrument_reservation'
    __table_args__ = (
        CheckConstraint(
            'released_at IS NULL OR released_at >= reserved_at',
            name='chk_instrument_reservation_released',
        ),
        # An instrument can only have one active reservation
        Index(
            'uk_instrument_reservation_instrument_active', 'instrument_id',
            unique=True, postgresql_where=text('active = TRUE'),
        ),
    )

    # Attributes
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, server_default=text('gen_random_uuid()'))

    # Timestamps
    reserved_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False, server_default=func.now())
    released_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True) # Nullable porque aún no se ha liberado

    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default=text('true'))

    # FKs
    operation_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('operation.id', ondelete='CASCADE'), nullable=False)
    instrument_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('instrument.id', ondelete='RESTRICT'), nullable=False)

    # Relations
    operation: Mapped["Operation"] = relationship("Operation", back_populates="instrument_reservations")
    instrument: Mapped["Instrument"] = relationship("Instrument", back_populates="reservations")
