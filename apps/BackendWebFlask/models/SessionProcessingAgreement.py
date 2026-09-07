from extensions import db
from sqlalchemy.dialects.postgresql import UUID, TIMESTAMP
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy import ForeignKey, Boolean
import uuid
from datetime import datetime


class SessionProcessingAgreement(db.Model):
    __tablename__ = 'session_processing_agreement'

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    session_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('work_session.id'), nullable=False, unique=True)
    privacy_notice_version_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('privacy_notice_version.id'), nullable=False)
    purpose_quality_ops: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    purpose_model_improvement: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    agreed_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    agreed_by_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey('user.id'), nullable=True)

    session: Mapped["WorkSession"] = relationship("WorkSession", back_populates="processing_agreement")
    privacy_notice_version: Mapped["PrivacyNoticeVersion"] = relationship(
        "PrivacyNoticeVersion", back_populates="agreements"
    )
    agreed_by_user: Mapped["User | None"] = relationship("User", back_populates="processing_agreements")