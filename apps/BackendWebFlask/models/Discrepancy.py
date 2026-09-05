from extensions import db
from sqlalchemy.dialects.postgresql import UUID, TIMESTAMP
from sqlalchemy.orm import Mapped,mapped_column,relationship
from sqlalchemy import ForeignKey, String, Text, Boolean
import uuid
from datetime import datetime, timezone

class Discrepancy(db.Model):
    __tablename__ = 'discrepancy'
    
    # Attributes
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    resolved: Mapped[bool] = mapped_column(Boolean,nullable=False)
    resolved_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    reason_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('cat_discrepancy_reason.id'), nullable=False)
    session_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('work_session.id'), nullable=True)
    origin_event_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('count_event.id'), nullable=False)
    
    # Relaciones
    reason: Mapped["CatDiscrepancyReason"] = relationship("CatDiscrepancyReason", back_populates="discrepancies") # Discrepancy -> CatDiscrepancyReason 'Reason'
    origin: Mapped["CountEvent"] = relationship("CountEvent", back_populates="discrepancies") # Discrepancy -> CountEvent 'origin'
    session: Mapped["WorkSession"] = relationship("WorkSession", back_populates="conflicts") # Discrepancy -> WorkSession 'session'