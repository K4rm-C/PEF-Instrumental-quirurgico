from extensions import db
from sqlalchemy.dialects.postgresql import UUID, TIMESTAMP
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy import ForeignKey, String, Boolean, SmallInteger
import uuid
from datetime import datetime, timezone

class Kit(db.Model):
    __tablename__='kit'
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(), nullable=False)
    version: Mapped[int] = mapped_column(SmallInteger(), nullable=False, default=1)
    active: Mapped[bool] = mapped_column(Boolean(), nullable=False, default=True)
    institution_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey('institution.id'), nullable=False)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False, default=datetime.now(timezone.utc))

    institution: Mapped["Institution"] = relationship("Institution", back_populates="kits")
    sessions: Mapped[list["WorkSession"]] = relationship("WorkSession", back_populates="kit")
    items: Mapped[list["KitItem"]] = relationship("KitItem", back_populates="kit")
    procedure_kits: Mapped[list["ProcedureKit"]] = relationship("ProcedureKit", back_populates="kit")