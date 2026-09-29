from extensions import db
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped,mapped_column,relationship
from sqlalchemy import ForeignKey, String, Boolean, UniqueConstraint, text
import uuid

class OperatingRoom(db.Model):
    __tablename__ = 'operating_room'
    __table_args__ = (
        UniqueConstraint('institution_id', 'code', name='uk_operating_room_institution_code'),
    )

    # Attributes
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, server_default=text('gen_random_uuid()'))
    code: Mapped[str] = mapped_column(String(32), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default=text('true'))
    institution_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('institution.id', ondelete='CASCADE'), nullable=False)

    # Relations
    # OperatingRoom -> Institution 'institution'
    stations: Mapped[list["CaptureStation"]] = relationship("CaptureStation", back_populates="room") # OperatingRoom << CaptureStation 'stations'
    operations: Mapped[list["Operation"]] = relationship("Operation", back_populates="location") # OperatingRoom << Operation 'operations'
