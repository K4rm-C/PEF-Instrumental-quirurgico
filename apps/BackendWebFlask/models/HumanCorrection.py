from extensions import db
from sqlalchemy.dialects.postgresql import UUID, TIMESTAMP
from sqlalchemy.orm import Mapped,mapped_column,relationship
from sqlalchemy import ForeignKey, Text, UniqueConstraint, func, text
import uuid
from datetime import datetime

class HumanCorrection(db.Model):
    __tablename__ = 'human_correction'
    __table_args__ = (
        UniqueConstraint('count_event_id', name='uk_human_correction_count_event'),
    )

    # Attributes
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, server_default=text('gen_random_uuid()'))
    justification: Mapped[str] = mapped_column(Text, nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False, server_default=func.now())
    count_event_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('count_event.id', ondelete='CASCADE'), nullable=False)
    user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey('user.id', ondelete='SET NULL'), nullable=True)

    # Relations
    review: Mapped["CountEvent"] = relationship("CountEvent", back_populates="reviews") # HumanCorrection -> CountEvent 'review'
    # HumanCorrection -> User 'user'
