from extensions import db
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy import String
import uuid

class CatSurgicalRole(db.Model):
    __tablename__ = 'cat_surgical_role'

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    code: Mapped[str] = mapped_column(String(), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(), nullable=False)

    operation_physicians: Mapped[list["OperationPhysician"]] = relationship("OperationPhysician", back_populates="surgical_role")
    