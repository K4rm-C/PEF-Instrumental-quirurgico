from extensions import db
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import Mapped,mapped_column,relationship
from sqlalchemy import ForeignKey, String, Boolean, text
import uuid

class CaptureStation(db.Model):
    __tablename__ = 'capture_station'

    # Attributes
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, server_default=text('gen_random_uuid()'))
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    roi: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default=text('true'))
    room_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('operating_room.id', ondelete='CASCADE'), nullable=False)

    # Relations
    room: Mapped["OperatingRoom"] = relationship("OperatingRoom", back_populates="stations") # CaptureStation -> OperatingRoom 'room'
    sessions: Mapped[list["WorkSession"]] = relationship("WorkSession", back_populates="station") # CaptureStation << WorkSession 'sessions'
