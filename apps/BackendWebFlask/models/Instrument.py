from extensions import db
from sqlalchemy.dialects.postgresql import UUID, TIMESTAMP
from sqlalchemy.orm import Mapped,mapped_column,relationship
from sqlalchemy import ForeignKey, String, Boolean, Index, func, text
import uuid
from datetime import datetime

class Instrument(db.Model):
    __tablename__ = 'instrument'
    __table_args__ = (
        Index(
            'uk_instrument_institution_internal_code', 'institution_id', 'internal_code',
            unique=True, postgresql_where=text('internal_code IS NOT NULL'),
        ),
    )

    # Attributes
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, server_default=text('gen_random_uuid()'))
    internal_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    family_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('instrument_family.id', ondelete='RESTRICT'), nullable=False)
    cycle_status_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('cat_instrument_cycle_status.id', ondelete='RESTRICT'), nullable=False)
    institution_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('institution.id', ondelete='RESTRICT'), nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default=text('true'))
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())

    # Relations
    family: Mapped["InstrumentFamily"] = relationship("InstrumentFamily") # Instrument -> InstrumentFamily 'family'
    cycle_status: Mapped["CatInstrumentCycleStatus"] = relationship("CatInstrumentCycleStatus", back_populates="instruments") # Instrument -> CatInstrumentCycleStatus 'cycle_status'
    institution: Mapped["Institution"] = relationship("Institution", back_populates="instruments") # Instrument -> Institution 'institution'
    history: Mapped[list["InstrumentCycleEvent"]] = relationship("InstrumentCycleEvent", back_populates="instrument") # Instrument << InstrumentCycleEvent 'history'
    reservations: Mapped[list["InstrumentReservation"]] = relationship("InstrumentReservation", back_populates="instrument") # Instrument << InstrumentReservation 'reservations'
    usages: Mapped[list["InstrumentUsage"]] = relationship("InstrumentUsage", back_populates="instrument") # Instrument << InstrumentUsage 'usages'
