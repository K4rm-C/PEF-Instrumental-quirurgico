from extensions import db
from sqlalchemy.dialects.postgresql import UUID, TIMESTAMP, JSONB
from sqlalchemy.orm import Mapped,mapped_column,relationship
from sqlalchemy import ForeignKey,String,Text, SMALLINT
import uuid
from datetime import datetime, timezone

class CountEvent(db.Model): # Missing String size
    __tablename__ = 'count_event'
    
    # Attributes
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    event_type_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('cat_event_type.id'), nullable=False)
    client_event_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    payload: Mapped[str] = mapped_column(JSONB, nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    session_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('work_session.id'), nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('user.id'), nullable=False)
    expected_quantity: Mapped[int] = mapped_column(SMALLINT, nullable=False)
    detected_quantity: Mapped[int] = mapped_column(SMALLINT, nullable=False) 
    family_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('instrument_family.id'), nullable=False)
    
    # Relations
    session: Mapped["WorkSession"] = relationship("WorkSession", back_populates="audit") # CountEvent -> WorkSession 'session'
    user: Mapped["User"] = relationship("User") # CountEvent -> User 'user'
    reviews: Mapped[list["HumanCorrection"]] = relationship("HumanCorrection", back_populates="review") # CountEvent << HumanCorrection 'reviews'
    discrepancies: Mapped[list["Discrepancy"]] = relationship("Discrepancy", back_populates="origin") # CountEvent << Discrepancy 'discrepancies'
    event_type: Mapped["CatEventType"] = relationship("CatEventType", back_populates="events")