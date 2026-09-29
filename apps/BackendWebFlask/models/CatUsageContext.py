from extensions import db
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy import String, UniqueConstraint, text
import uuid


class CatUsageContext(db.Model):
    __tablename__ = 'cat_usage_context'
    __table_args__ = (
        UniqueConstraint('code', name='uk_cat_usage_context_code'),
    )

    # Attributes
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, server_default=text('gen_random_uuid()'))
    code: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)

    # Relations
    usages: Mapped[list["InstrumentUsage"]] = relationship("InstrumentUsage", back_populates="context") # CatUsageContext << InstrumentUsage 'usages'
