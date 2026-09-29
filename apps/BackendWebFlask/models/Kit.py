from extensions import db
from sqlalchemy.dialects.postgresql import UUID, TIMESTAMP
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy import ForeignKey, String, Boolean, SmallInteger, CheckConstraint, UniqueConstraint, func, text
import uuid
from datetime import datetime

class Kit(db.Model):
    __tablename__='kit'
    __table_args__ = (
        UniqueConstraint('institution_id', 'name', 'version', name='uk_kit_institution_name_version'),
        CheckConstraint('version > 0', name='chk_kit_version'),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, server_default=text('gen_random_uuid()'))
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    version: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=1, server_default=text('1'))
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default=text('true'))
    institution_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('institution.id', ondelete='CASCADE'), nullable=False)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())

    institution: Mapped["Institution"] = relationship("Institution", back_populates="kits")
    sessions: Mapped[list["WorkSession"]] = relationship("WorkSession", back_populates="kit")
    items: Mapped[list["KitItem"]] = relationship("KitItem", back_populates="kit")
    procedure_kits: Mapped[list["ProcedureKit"]] = relationship("ProcedureKit", back_populates="kit")
