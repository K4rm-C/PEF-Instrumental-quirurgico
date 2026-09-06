from extensions import db
from sqlalchemy.dialects.postgresql import UUID, TIMESTAMP, JSONB
from sqlalchemy.orm import Mapped,mapped_column,relationship
from sqlalchemy import ForeignKey, String, Text, Boolean, SMALLINT
import uuid
from datetime import datetime, timezone

class ModelClass(db.Model):
    __tablename__ = 'model_class'
    
    # Attributes
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    model_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('yolo_model.id'), nullable=False)
    family_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('instrument_family.id'), nullable=False)
    yolo_class_id: Mapped[int] = mapped_column(SMALLINT, nullable=False)
    
    # Relations
    model: Mapped["YoloModel"] = relationship("YoloModel", back_populates="mapping") # ModelClass << YoloModel 'model'
    family: Mapped["InstrumentFamily"] = relationship("InstrumentFamily", back_populates='yolo_class') # ModelClass -> InstrumentFamily 'family'
