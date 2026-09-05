from extensions import db
from sqlalchemy.dialects.postgresql import UUID, TIMESTAMP
from sqlalchemy.orm import Mapped,mapped_column,relationship
from sqlalchemy import ForeignKey, String, Text, Boolean
import uuid
from datetime import datetime, timezone

class OperatingRoom(db.Model):
    __tablename__ = 'operating_room'
    
    # Attributes
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    code: Mapped[str] = mapped_column(String(), nullable=False)
    name: Mapped[str] = mapped_column(String(), nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False)
    institution_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('institution.id'), nullable=False)
    
    # Relations
    # OperatingRoom -> Institution 'institution'
    stations: Mapped[list["CaptureStation"]] = relationship("CaptureStation", back_populates="room") # OperatingRoom << CaptureStation 'stations'
    operations: Mapped[list["Operation"]] = relationship("Operation", back_populates="location") # OperatingRoom << Operation 'operations'