from extensions import db
from sqlalchemy.dialects.postgresql import UUID, TIMESTAMP
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy import String, Text, Boolean, CHAR, UniqueConstraint, func, text
import uuid
from datetime import datetime


class PrivacyNoticeVersion(db.Model):
    __tablename__ = 'privacy_notice_version'
    __table_args__ = (
        UniqueConstraint('version', name='uk_privacy_notice_version'),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, server_default=text('gen_random_uuid()'))
    version: Mapped[str] = mapped_column(String(32), nullable=False)
    effective_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    document_uri: Mapped[str] = mapped_column(Text, nullable=False)
    content_sha256: Mapped[str | None] = mapped_column(CHAR(64), nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default=text('true'))
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False, server_default=func.now())

    agreements: Mapped[list["SessionProcessingAgreement"]] = relationship(
        "SessionProcessingAgreement", back_populates="privacy_notice_version"
    )
