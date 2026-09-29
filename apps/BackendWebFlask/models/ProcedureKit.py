from extensions import db
from sqlalchemy.dialects.postgresql import UUID, TIMESTAMP
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy import ForeignKey, String, Boolean, Index, UniqueConstraint, func, text
import uuid
from datetime import datetime


class ProcedureKit(db.Model):
    __tablename__ = 'procedure_kit'
    __table_args__ = (
        UniqueConstraint('procedure_type_id', 'kit_id', name='uk_procedure_kit_type_kit'),
        # At most one active default kit per procedure type
        Index(
            'uk_procedure_kit_default', 'procedure_type_id',
            unique=True, postgresql_where=text('is_default = TRUE AND active = TRUE'),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, server_default=text('gen_random_uuid()'))
    procedure_type_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('cat_procedure_type.id', ondelete='CASCADE'), nullable=False)
    kit_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('kit.id', ondelete='RESTRICT'), nullable=False)
    technique_label: Mapped[str | None] = mapped_column(String(160), nullable=True)
    is_default: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=text('false'))
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default=text('true'))
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())

    procedure_type: Mapped["CatProcedureType"] = relationship("CatProcedureType", back_populates="procedure_kits")
    kit: Mapped["Kit"] = relationship("Kit", back_populates="procedure_kits")
