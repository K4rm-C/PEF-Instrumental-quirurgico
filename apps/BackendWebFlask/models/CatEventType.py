from extensions import db
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy import String, UniqueConstraint, text
import uuid


class CatEventType(db.Model):
    __tablename__ = 'cat_event_type'
    __table_args__ = (
        UniqueConstraint('code', name='uk_cat_event_type_code'),
    )

    # Attributes
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, server_default=text('gen_random_uuid()'))
    code: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(160), nullable=False)

    # Relations
    events: Mapped[list["CountEvent"]] = relationship("CountEvent", back_populates="event_type")
