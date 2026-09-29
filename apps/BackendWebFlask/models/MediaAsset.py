from extensions import db
from sqlalchemy.dialects.postgresql import UUID, TIMESTAMP
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy import Text, String, BigInteger, CHAR, CheckConstraint, UniqueConstraint, func, text
import uuid
from datetime import datetime

class MediaAsset(db.Model):
    __tablename__ = 'media_asset'
    __table_args__ = (
        UniqueConstraint('bucket', 'object_key', name='uk_media_asset_bucket_object_key'),
        CheckConstraint('size_bytes IS NULL OR size_bytes >= 0', name='chk_media_asset_size_bytes'),
        CheckConstraint("kind IN ('jpeg', 'gif', 'weights', 'other')", name='chk_media_asset_kind'),
        CheckConstraint("storage_provider IN ('gcs', 'minio', 'other')", name='chk_media_asset_storage_provider'),
        CheckConstraint(
            'purged_at IS NULL OR purge_requested_at IS NULL OR purged_at >= purge_requested_at',
            name='chk_media_asset_purge_order',
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, server_default=text('gen_random_uuid()'))
    gcs_uri: Mapped[str] = mapped_column(Text, nullable=False)
    bucket: Mapped[str] = mapped_column(String(128), nullable=False)
    object_key: Mapped[str] = mapped_column(Text, nullable=False)
    content_type: Mapped[str | None] = mapped_column(String(128), nullable=True)
    kind: Mapped[str] = mapped_column(String(32), nullable=False, default='jpeg', server_default=text("'jpeg'"))
    storage_provider: Mapped[str] = mapped_column(String(32), nullable=False, default='gcs', server_default=text("'gcs'"))
    sha256: Mapped[str | None] = mapped_column(CHAR(64), nullable=True)
    size_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    retention_until: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    purge_requested_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    purged_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    blocked_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())

    # Relación con YoloModel
    yolo_models: Mapped[list["YoloModel"]] = relationship("YoloModel", back_populates="media_asset")
