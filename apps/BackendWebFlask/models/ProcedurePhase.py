from extensions import db
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy import ForeignKey, Boolean, SmallInteger, CheckConstraint, UniqueConstraint, text
import uuid


class ProcedurePhase(db.Model):
    __tablename__ = 'procedure_phase'
    __table_args__ = (
        UniqueConstraint('procedure_type_id', 'phase_id', name='uk_procedure_phase_type_phase'),
        UniqueConstraint('procedure_type_id', 'sort_order', name='uk_procedure_phase_type_sort'),
        CheckConstraint('sort_order > 0', name='chk_procedure_phase_sort_order'),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, server_default=text('gen_random_uuid()'))
    procedure_type_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('cat_procedure_type.id', ondelete='CASCADE'), nullable=False)
    phase_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('cat_operation_phase.id', ondelete='RESTRICT'), nullable=False)
    sort_order: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    is_count_required: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default=text('true'))
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default=text('true'))

    procedure_type: Mapped["CatProcedureType"] = relationship("CatProcedureType", back_populates="procedure_phases")
    phase: Mapped["CatOperationPhase"] = relationship("CatOperationPhase", back_populates="procedure_phases")
