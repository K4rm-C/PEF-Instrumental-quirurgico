from extensions import db
from sqlalchemy.dialects.postgresql import UUID, TIMESTAMP
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy import ForeignKey, UniqueConstraint
import uuid
from datetime import datetime, timezone


class UserOperatingRoom(db.Model):
    __tablename__ = 'user_operating_room'
    __table_args__ = (
        UniqueConstraint('user_id', 'room_id', name='uk_user_operating_room_user_room'),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('user.id', ondelete='CASCADE'), nullable=False)
    room_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('operating_room.id', ondelete='CASCADE'), nullable=False)
    assigned_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    user: Mapped["User"] = relationship("User", back_populates="room_assignments")
    room: Mapped["OperatingRoom"] = relationship("OperatingRoom", back_populates="user_assignments")
