from extensions import db
from sqlalchemy.dialects.postgresql import UUID, TIMESTAMP, JSONB
from sqlalchemy.orm import Mapped,mapped_column,relationship
from sqlalchemy import ForeignKey,String,Text
import uuid
from datetime import datetime, timezone

class CountEvent(db.Model): # Missing String size
    __tablename__ = 'count_event'
    
    # Attributes
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    event_type: Mapped[str] = mapped_column(String(), nullable=True)
    payload: Mapped[str] = mapped_column(JSONB, nullable=True)
    occurred_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    session_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('work_session.id'), nullable=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('user.id'), nullable=True)
    
    # Relations
    session: Mapped["WorkSession"] = relationship("WorkSession", back_populates="audit") # CountEvent -> WorkSession 'session'
    # CountEvent -> User 'user'
    reviews: Mapped[list["HumanCorrection"]] = relationship("HumanCorrection", back_populates="review") # CountEvent << HumanCorrection 'reviews'
    discrepancies: Mapped[list["Discrepancy"]] = relationship("Discrepancy", back_populates="origin") # CountEvent << Discrepancy 'discrepancies'