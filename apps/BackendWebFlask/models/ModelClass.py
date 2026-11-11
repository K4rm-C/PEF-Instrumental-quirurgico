from extensions import db
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy import ForeignKey, SmallInteger
import uuid

class ModelClass(db.Model):
    __tablename__ = 'model_class'

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    model_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('yolo_model.id'), nullable=False)
    family_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('instrument_family.id'), nullable=False)
    yolo_class_id: Mapped[int] = mapped_column(SmallInteger, nullable=False) 

    # Relaciones
    model: Mapped["YoloModel"] = relationship("YoloModel", back_populates="model_classes")
    family: Mapped["InstrumentFamily"] = relationship("InstrumentFamily", back_populates="model_classes")