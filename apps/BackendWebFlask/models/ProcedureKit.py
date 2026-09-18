from extensions import db
from sqlalchemy.dialects.postgresql import UUID, TIMESTAMP
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy import ForeignKey, String, Boolean
import uuid
from datetime import datetime


class ProcedureKit(db.Model):
    __tablename__ = 'procedure_kit'

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    procedure_type_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('cat_procedure_type.id'), nullable=False)
    kit_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('kit.id'), nullable=False)
    technique_label: Mapped[str | None] = mapped_column(String(160), nullable=True)
    is_default: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)

    procedure_type: Mapped["CatProcedureType"] = relationship("CatProcedureType", back_populates="procedure_kits")
    kit: Mapped["Kit"] = relationship("Kit", back_populates="procedure_kits")