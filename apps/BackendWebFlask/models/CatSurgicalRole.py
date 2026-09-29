from extensions import db
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy import String, UniqueConstraint, text
import uuid


class CatSurgicalRole(db.Model):
    __tablename__ = 'cat_surgical_role'
    __table_args__ = (
        UniqueConstraint('code', name='uk_cat_surgical_role_code'),
    )

    # Attributes
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, server_default=text('gen_random_uuid()'))
    code: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)

    # Relations
    operation_physicians: Mapped[list["OperationPhysician"]] = relationship("OperationPhysician", back_populates="surgical_role")
