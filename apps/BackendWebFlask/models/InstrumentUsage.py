from extensions import db
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped,mapped_column,relationship
from sqlalchemy import ForeignKey, Text, Boolean, UniqueConstraint, text
import uuid

class InstrumentUsage(db.Model):
    __tablename__ = 'instrument_usage'
    __table_args__ = (
        UniqueConstraint('instrument_id', 'procedure_type_id', 'context_id', name='uk_instrument_usage_triple'),
    )

    # Attributes
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, server_default=text('gen_random_uuid()'))
    instrument_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('instrument.id', ondelete='CASCADE'), nullable=False)
    procedure_type_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('cat_procedure_type.id', ondelete='RESTRICT'), nullable=False)
    context_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('cat_usage_context.id', ondelete='RESTRICT'), nullable=False)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default=text('true'))

    # Relations
    instrument: Mapped["Instrument"] = relationship("Instrument", back_populates="usages") # InstrumentUsage -> Instrument 'instrument'
    procedure_type: Mapped["CatProcedureType"] = relationship("CatProcedureType", back_populates="instrument_usage") # InstrumentUsage -> CatProcedureType 'procedure_type'
    context: Mapped["CatUsageContext"] = relationship("CatUsageContext", back_populates="usages") # InstrumentUsage -> CatUsageContext 'context'
