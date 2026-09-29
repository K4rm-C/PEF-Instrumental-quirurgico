from extensions import db
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy import String, UniqueConstraint, text
import uuid


class CatSessionStatus(db.Model):
    __tablename__ = 'cat_session_status'
    __table_args__ = (
        UniqueConstraint('code', name='uk_cat_session_status_code'),
    )

    # Attributes
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, server_default=text('gen_random_uuid()'))
    code: Mapped[str] = mapped_column(String(32), nullable=False)
    name: Mapped[str] = mapped_column(String(80), nullable=False)

    # Relations
    sessions: Mapped[list["WorkSession"]] = relationship("WorkSession", back_populates="status") # CatSessionStatus << WorkSessions 'sessions'
