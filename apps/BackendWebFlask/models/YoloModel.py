from extensions import db
from sqlalchemy.dialects.postgresql import UUID, TIMESTAMP
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy import ForeignKey, String, CHAR, Boolean
import uuid
from datetime import datetime, timezone

class YoloModel(db.Model):
    __tablename__ = 'yolo_model'

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    version_tag: Mapped[str] = mapped_column(String, unique=True, nullable=False)  
    media_asset_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('media_asset.id'), nullable=False)
    checksum: Mapped[str] = mapped_column(CHAR(64), nullable=False)                
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    published_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))

    # Relaciones
    media_asset: Mapped["MediaAsset"] = relationship("MediaAsset", back_populates="yolo_models")
    model_classes: Mapped[list["ModelClass"]] = relationship("ModelClass", back_populates="model")