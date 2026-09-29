from extensions import db
from sqlalchemy.dialects.postgresql import UUID, TIMESTAMP, JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy import ForeignKey, String, Boolean, Text, CheckConstraint, UniqueConstraint, func, text
import uuid
from datetime import datetime

class User(db.Model):
    __tablename__ = 'user'
    __table_args__ = (
        UniqueConstraint('email', name='uk_user_email'),
        CheckConstraint("jsonb_typeof(ui_preferences) = 'object'", name='chk_user_ui_preferences_object'),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, server_default=text('gen_random_uuid()'))
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default=text('true'))
    email: Mapped[str] = mapped_column(String(255), nullable=False)
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)
    password_updated_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    last_login_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    ui_preferences: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default=text('\'{"locale":"en","theme":"light"}\'::jsonb')
    )
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())
    institution_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('institution.id', ondelete='RESTRICT'), nullable=False)

    user_roles: Mapped[list["UserRole"]] = relationship("UserRole", back_populates="user")
    processing_agreements: Mapped[list["SessionProcessingAgreement"]] = relationship("SessionProcessingAgreement", back_populates="agreed_by_user")
    access_audits: Mapped[list["AccessAudit"]] = relationship("AccessAudit", back_populates="actor_user")
    handled_privacy_requests: Mapped[list["PrivacyRequest"]] = relationship("PrivacyRequest", back_populates="handled_by_user")
    # work_session has two FKs to user: opener (user_id) and closer (closed_by_user_id)
    work_sessions: Mapped[list["WorkSession"]] = relationship(
        "WorkSession", back_populates="user", foreign_keys="WorkSession.user_id"
    )
    closed_work_sessions: Mapped[list["WorkSession"]] = relationship(
        "WorkSession", back_populates="closed_by_user", foreign_keys="WorkSession.closed_by_user_id"
    )
