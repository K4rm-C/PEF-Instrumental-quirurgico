from extensions import db
from sqlalchemy.dialects.postgresql import UUID, TIMESTAMP
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy import ForeignKey, String, Text, CheckConstraint
import uuid
from datetime import datetime


class PrivacyRequest(db.Model):
    __tablename__ = 'privacy_request'
    __table_args__ = (
        CheckConstraint(
            "request_type IN ('access', 'rectify', 'cancel', 'oppose')",
            name='chk_privacy_request_type',
        ),
        CheckConstraint(
            "status IN ('received', 'in_progress', 'completed', 'denied')",
            name='chk_privacy_request_status',
        ),
        CheckConstraint(
            "status NOT IN ('completed', 'denied') OR completed_at IS NOT NULL",
            name='chk_privacy_request_completed',
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    request_type: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default='received')
    channel: Mapped[str | None] = mapped_column(String(32), nullable=True)
    subject_patient_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('patient.id'), nullable=False)
    requested_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    due_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    handled_by_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey('user.id'), nullable=True)
    resolution_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)

    subject_patient: Mapped["Patient"] = relationship("Patient", back_populates="privacy_requests")
    handled_by_user: Mapped["User | None"] = relationship("User", back_populates="handled_privacy_requests")