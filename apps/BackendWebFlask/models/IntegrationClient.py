from extensions import db
from sqlalchemy.dialects.postgresql import UUID, TIMESTAMP, JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy import ForeignKey, String, Text, Boolean, UniqueConstraint, func, text
import uuid
from datetime import datetime


class IntegrationClient(db.Model):
    __tablename__ = 'integration_client'
    __table_args__ = (
        UniqueConstraint('client_id', name='uk_integration_client_client_id'),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, server_default=text('gen_random_uuid()'))
    client_id: Mapped[str] = mapped_column(String(128), nullable=False)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    secret_hash: Mapped[str] = mapped_column(Text, nullable=False)
    scopes: Mapped[dict | list] = mapped_column(JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb"))
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default=text('true'))
    last_used_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())
    institution_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('institution.id', ondelete='CASCADE'), nullable=False)

    institution: Mapped["Institution"] = relationship("Institution", back_populates="integration_clients")
    access_audits: Mapped[list["AccessAudit"]] = relationship("AccessAudit", back_populates="actor_client")
