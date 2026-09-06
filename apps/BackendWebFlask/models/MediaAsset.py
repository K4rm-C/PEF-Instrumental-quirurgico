from extensions import db
from sqlalchemy.dialects.postgresql import UUID, TIMESTAMP, JSONB
from sqlalchemy.orm import Mapped,mapped_column,relationship
from sqlalchemy import ForeignKey, String, Text, Boolean, CHAR, BIGINT
import uuid
from datetime import datetime, timezone

class MediaAsset(db.Model):
    __tablename__ = 'media_asset'
    
    # Attributes
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    gcs_uri: Mapped[str] = mapped_column(Text, nullable=False)
    bucket: Mapped[str] = mapped_column(String(), nullable=False)
    object_key: Mapped[str] = mapped_column(Text, nullable=False)
    sha256: Mapped[str] = mapped_column(CHAR, nullable=False)
    size_bytes: Mapped[int] = mapped_column(BIGINT, nullable=False)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    
    # Relations
    weights: Mapped[list["YoloModel"]] = relationship("YoloModel", back_populates="media_asset") # MediaAsset << YoloModel 'weights'
    image: Mapped[list["FamilyExample"]] = relationship("FamilyExaple", back_populates="media_asset") # MediaAsset << FamilyExample 'image'