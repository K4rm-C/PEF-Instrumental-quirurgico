from extensions import db
from sqlalchemy.dialects.postgresql import UUID, TIMESTAMP
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy import ForeignKey, UniqueConstraint, func, text
import uuid
from datetime import datetime

class UserRole(db.Model):
    __tablename__ = 'user_role'
    __table_args__ = (
        UniqueConstraint('user_id', 'role_id', name='uk_user_role_user_role'),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, server_default=text('gen_random_uuid()'))
    assigned_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False, server_default=func.now())
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('user.id', ondelete='CASCADE'), nullable=False)
    role_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('role.id', ondelete='CASCADE'), nullable=False)

    role: Mapped["Role"] = relationship("Role", back_populates="user_roles")
    user: Mapped["User"] = relationship("User", back_populates="user_roles")
