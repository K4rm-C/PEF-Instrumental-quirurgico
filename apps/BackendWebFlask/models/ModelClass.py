from extensions import db
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy import ForeignKey, SmallInteger, CheckConstraint, UniqueConstraint, text
import uuid

class ModelClass(db.Model):
    __tablename__ = 'model_class'
    __table_args__ = (
        UniqueConstraint('model_id', 'yolo_class_id', name='uk_model_class_model_yolo_id'),
        UniqueConstraint('model_id', 'family_id', name='uk_model_class_model_family'),
        CheckConstraint('yolo_class_id >= 0', name='chk_model_class_yolo_id'),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, server_default=text('gen_random_uuid()'))
    model_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('yolo_model.id', ondelete='CASCADE'), nullable=False)
    family_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('instrument_family.id', ondelete='RESTRICT'), nullable=False)
    yolo_class_id: Mapped[int] = mapped_column(SmallInteger, nullable=False)

    # Relaciones
    model: Mapped["YoloModel"] = relationship("YoloModel", back_populates="model_classes")
    family: Mapped["InstrumentFamily"] = relationship("InstrumentFamily", back_populates="model_classes")
