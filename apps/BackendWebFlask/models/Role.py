from extensions import db
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy import ForeignKey, String, UniqueConstraint, text
import uuid

class Role(db.Model):
    __tablename__ = 'role'
    __table_args__ = (
        # code is unique per institution, not globally
        UniqueConstraint('institution_id', 'code', name='uk_role_institution_code'),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, server_default=text('gen_random_uuid()'))
    code: Mapped[str] = mapped_column(String(64), nullable=False)
    description: Mapped[str] = mapped_column(String(255), nullable=False)

    institution_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('institution.id', ondelete='CASCADE'), nullable=False)
    institution: Mapped["Institution"] = relationship("Institution", back_populates="roles")

    user_roles: Mapped[list["UserRole"]] = relationship("UserRole", back_populates="role", cascade="all, delete-orphan")
