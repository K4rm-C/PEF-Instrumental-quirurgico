from extensions import db
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy import String, Boolean, UniqueConstraint, text
import uuid


class CatOperationPhase(db.Model):
    __tablename__ = 'cat_operation_phase'
    __table_args__ = (
        UniqueConstraint('code', name='uk_cat_operation_phase_code'),
    )

    # Attributes
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, server_default=text('gen_random_uuid()'))
    code: Mapped[str] = mapped_column(String(32), nullable=False)
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default=text('true'))

    # Relations
    procedure_phases: Mapped[list["ProcedurePhase"]] = relationship("ProcedurePhase", back_populates="phase")
    work_sessions: Mapped[list["WorkSession"]] = relationship("WorkSession", back_populates="current_phase")
