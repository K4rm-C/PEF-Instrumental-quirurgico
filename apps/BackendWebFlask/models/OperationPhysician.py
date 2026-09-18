from extensions import db
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy import ForeignKey
import uuid

class OperationPhysician(db.Model):
    __tablename__ = 'operation_physician'

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    operation_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey('operation.id'), nullable=False)
    physician_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey('physician.id'), nullable=False)
    surgical_role_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey('cat_surgical_role.id'), nullable=False)

    operation: Mapped["Operation"] = relationship("Operation", back_populates="operation_physicians")
    physician: Mapped["Physician"] = relationship("Physician", back_populates="operation_physicians")
    surgical_role: Mapped["CatSurgicalRole"] = relationship("CatSurgicalRole", back_populates="operation_physicians")