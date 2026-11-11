from extensions import db
from sqlalchemy.dialects.postgresql import UUID, TIMESTAMP, JSONB
from sqlalchemy.orm import Mapped,mapped_column,relationship
from sqlalchemy import ForeignKey, String, Text, Boolean
import uuid
from datetime import datetime, timezone

class CaptureStation(db.Model): # Missing String size
    __tablename__ = 'capture_station'
    
    # Attributes
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String, nullable=False)
    roi: Mapped[str] = mapped_column(JSONB, nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False)
    room_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('operating_room.id'), nullable=False)
    
    # Relations
    room: Mapped["OperatingRoom"] = relationship("OperatingRoom", back_populates="stations") # CaptureStation -> OperatingRoom 'room'
    sessions: Mapped[list["WorkSession"]] = relationship("WorkSession", back_populates="station") # CaptureStation << WorkSession 'sessions'