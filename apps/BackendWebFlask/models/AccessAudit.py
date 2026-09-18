from extensions import db
from sqlalchemy.dialects.postgresql import UUID, TIMESTAMP, INET
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy import ForeignKey, String, CheckConstraint
import uuid
from datetime import datetime


class AccessAudit(db.Model):
    __tablename__ = 'access_audit'
    __table_args__ = (
        CheckConstraint(
            "actor_type IN ('user', 'client')",
            name='chk_access_audit_actor_type',
        ),
        CheckConstraint(
            "(actor_type = 'user' AND actor_user_id IS NOT NULL AND actor_client_id IS NULL) OR "
            "(actor_type = 'client' AND actor_client_id IS NOT NULL AND actor_user_id IS NULL)",
            name='chk_access_audit_actor_pair',
        ),
        CheckConstraint("outcome IN ('success', 'denied')", name='chk_access_audit_outcome'),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    occurred_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    actor_type: Mapped[str] = mapped_column(String(16), nullable=False)
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey('user.id'), nullable=True)
    actor_client_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey('integration_client.id'), nullable=True)
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    resource_type: Mapped[str] = mapped_column(String(64), nullable=False)
    resource_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    institution_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey('institution.id'), nullable=True)
    outcome: Mapped[str] = mapped_column(String(16), nullable=False, default='success')
    correlation_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    ip: Mapped[str | None] = mapped_column(INET, nullable=True)

    actor_user: Mapped["User | None"] = relationship("User", back_populates="access_audits")
    actor_client: Mapped["IntegrationClient | None"] = relationship("IntegrationClient", back_populates="access_audits")
    institution: Mapped["Institution | None"] = relationship("Institution", back_populates="access_audits")