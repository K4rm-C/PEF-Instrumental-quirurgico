from extensions import db
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy import ForeignKey, String, Boolean
import uuid

class User(db.Model):
    __tablename__ = 'user'

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(), nullable=False)
    active: Mapped[bool] = mapped_column(Boolean(), nullable=False, default=True)
    email: Mapped[str] = mapped_column(String(), unique=True, nullable=False)
    institution_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey('institution.id'), nullable=False)
    
    user_roles: Mapped[list["UserRole"]] = relationship("UserRole", back_populates="user")
    processing_agreements: Mapped[list["SessionProcessingAgreement"]] = relationship("SessionProcessingAgreement", back_populates="agreed_by_user")
    access_audits: Mapped[list["AccessAudit"]] = relationship("AccessAudit", back_populates="actor_user")
    handled_privacy_requests: Mapped[list["PrivacyRequest"]] = relationship("PrivacyRequest", back_populates="handled_by_user")