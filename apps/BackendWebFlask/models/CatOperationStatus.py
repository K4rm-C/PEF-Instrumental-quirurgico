from extensions import db
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy import String, UniqueConstraint, text
import uuid


class CatOperationStatus(db.Model):
    __tablename__ = 'cat_operation_status'
    __table_args__ = (
        UniqueConstraint('code', name='uk_cat_operation_status_code'),
    )

    # Attributes
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, server_default=text('gen_random_uuid()'))
    code: Mapped[str] = mapped_column(String(32), nullable=False)
    name: Mapped[str] = mapped_column(String(80), nullable=False)

    # Relations
    operations: Mapped[list["Operation"]] = relationship("Operation", back_populates="status") # CatOperationStatus << Operation 'operations'
