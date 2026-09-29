from extensions import db
from sqlalchemy.dialects.postgresql import UUID, TIMESTAMP
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy import ForeignKey, String, CHAR, Boolean, Index, UniqueConstraint, func, text
import uuid
from datetime import datetime

class YoloModel(db.Model):
    __tablename__ = 'yolo_model'
    __table_args__ = (
        UniqueConstraint('version_tag', name='uk_yolo_model_version_tag'),
        # Only one active model at a time
        Index('uk_yolo_model_active', 'active', unique=True, postgresql_where=text('active = TRUE')),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, server_default=text('gen_random_uuid()'))
    version_tag: Mapped[str] = mapped_column(String(32), nullable=False)
    media_asset_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey('media_asset.id', ondelete='SET NULL'), nullable=True)
    checksum: Mapped[str | None] = mapped_column(CHAR(64), nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=text('false'))
    published_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False, server_default=func.now())

    # Relaciones
    media_asset: Mapped["MediaAsset | None"] = relationship("MediaAsset", back_populates="yolo_models")
    model_classes: Mapped[list["ModelClass"]] = relationship("ModelClass", back_populates="model")
