from extensions import db
from sqlalchemy.dialects.postgresql import UUID, TIMESTAMP
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy import Text, String, BigInteger, CHAR
import uuid
from datetime import datetime, timezone

class MediaAsset(db.Model):
    __tablename__ = 'media_asset'

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    gcs_uri: Mapped[str] = mapped_column(Text, nullable=False)            
    bucket: Mapped[str] = mapped_column(String, nullable=False)            
    object_key: Mapped[str] = mapped_column(Text, nullable=False)          
    content_type: Mapped[str] = mapped_column(String, nullable=False)      
    sha256: Mapped[str] = mapped_column(CHAR(64), nullable=False)          
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)    
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))

    
    yolo_models: Mapped[list["YoloModel"]] = relationship("YoloModel", back_populates="media_asset")