from extensions import db
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy import String, Boolean
import uuid


class CatOperationPhase(db.Model):
    __tablename__ = 'cat_operation_phase'

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    code: Mapped[str] = mapped_column(String(32), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    procedure_phases: Mapped[list["ProcedurePhase"]] = relationship(
        "ProcedurePhase", back_populates="phase"
    )
    work_sessions: Mapped[list["WorkSession"]] = relationship(
        "WorkSession", back_populates="current_phase"
    )