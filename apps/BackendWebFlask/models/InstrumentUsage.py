from extensions import db
from sqlalchemy.dialects.postgresql import UUID, TIMESTAMP, JSONB
from sqlalchemy.orm import Mapped,mapped_column,relationship
from sqlalchemy import ForeignKey, String, Text, Boolean
import uuid
from datetime import datetime, timezone

class InstrumentUsage(db.Model):
    __tablename__ = 'instrument_usage'
    
    # Attributes
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    instrument_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('instrument.id'), nullable=False)
    procedure_type_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('cat_procedure_type.id'), nullable=False)
    context_id: Mapped[uuid.UUID] = mapped_column('cat_usage_context.id', nullable=False)
    notes: Mapped[str] = mapped_column(Text, nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False)
    
    # Relations
    instrument; Mapped["Instrument"] = relationship("Instrument", back_populates="usages") # InstrumentUsage -> Instrument 'instrument'
    procedure_type: Mapped["CatProcedureType"] = relationship("CatProcedureType", back_populates="instrument_usage") # InstrumentUsage -> CatProcedureType 'procedure_type'
    context: Mapped["CatUsageContext"] = relationship("CatUsageContext", back_populates="usages") # InstrumentUsage -> CatUsageContext 'context'