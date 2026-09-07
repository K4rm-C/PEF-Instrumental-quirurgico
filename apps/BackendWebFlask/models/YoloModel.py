from extensions import db
<<<<<<< HEAD
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
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    published_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))

    # Relaciones
    media_asset: Mapped["MediaAsset"] = relationship("MediaAsset", back_populates="yolo_models")
=======
from sqlalchemy.dialects.postgresql import UUID, TIMESTAMP, JSONB
from sqlalchemy.orm import Mapped,mapped_column,relationship
from sqlalchemy import ForeignKey, String, Text, Boolean, CHAR
import uuid
from datetime import datetime, timezone

class YoloModel(db.Model): # Missing String size
    __tablename__ = 'yolo_model'
    
    # Attributes
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    version_tag: Mapped[str] = mapped_column(String(), unique=True, nullable=False)
    media_asset_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('media_asset.id'), nullable=False)
    checksum: Mapped[str] = mapped_column(CHAR, nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False)
    published_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    
    # Relations
    mapping: Mapped[list["ModelClass"]] = relationship("ModelClass", back_populates="model") # YoloModel << ModelClass 'mapping'
    media_asset: Mapped["MediaAssets"] = relationship("MediaAssets", back_populates="weights") # YoloModel -> MediaAsset 'media_asset'
>>>>>>> 2cd0f5a588cc2184b066b4c8d191b93280e21801
