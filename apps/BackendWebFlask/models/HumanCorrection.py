from extensions import db
from sqlalchemy.dialects.postgresql import UUID, TIMESTAMP
from sqlalchemy.orm import Mapped,mapped_column,relationship
from sqlalchemy import ForeignKey, String, Text, Boolean
import uuid
from datetime import datetime, timezone

class HumanCorrection(db.Model):
    __tablename__ = 'HumanCorrection'
    
    # Attributes
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True,  default=uuid.uuid4)
    justification: Mapped[str] = mapped_column(Text, nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    count_event_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('count_event.id'), nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('user.id'), nullable=False)
    
    # Relations
    review: Mapped["CountEvent"] = relationship("CountEvent", back_populates="reviews") # HumanCorrection -> CountEvent 'review'
    # HumanCorrection -> User 'user'