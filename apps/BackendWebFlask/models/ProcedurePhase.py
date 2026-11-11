from extensions import db
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy import ForeignKey, Boolean, SmallInteger
import uuid


class ProcedurePhase(db.Model):
    __tablename__ = 'procedure_phase'

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    procedure_type_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('cat_procedure_type.id'), nullable=False)
    phase_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('cat_operation_phase.id'), nullable=False)
    sort_order: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    is_count_required: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    procedure_type: Mapped["CatProcedureType"] = relationship("CatProcedureType", back_populates="procedure_phases")
    phase: Mapped["CatOperationPhase"] = relationship("CatOperationPhase", back_populates="procedure_phases")