from extensions import db
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy import String, UniqueConstraint, text
import uuid


class CatProcedureType(db.Model):
    __tablename__ = 'cat_procedure_type'
    __table_args__ = (
        UniqueConstraint('code', name='uk_cat_procedure_type_code'),
    )

    # Attributes
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, server_default=text('gen_random_uuid()'))
    code: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)

    # Relations
    operations: Mapped[list["Operation"]] = relationship("Operation", back_populates="procedure_type") # CatProcedureType << Operation 'operations'
    instrument_usage: Mapped[list["InstrumentUsage"]] = relationship("InstrumentUsage", back_populates="procedure_type") # CatProcedureType << InstrumentUsage 'instrument_usage'
    procedure_kits: Mapped[list["ProcedureKit"]] = relationship("ProcedureKit", back_populates="procedure_type")
    procedure_phases: Mapped[list["ProcedurePhase"]] = relationship("ProcedurePhase", back_populates="procedure_type")
