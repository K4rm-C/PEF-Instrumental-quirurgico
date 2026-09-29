from extensions import db
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy import ForeignKey, UniqueConstraint, text
import uuid

class OperationPhysician(db.Model):
    __tablename__ = 'operation_physician'
    __table_args__ = (
        UniqueConstraint('operation_id', 'physician_id', 'surgical_role_id', name='uk_operation_physician_role'),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, server_default=text('gen_random_uuid()'))
    operation_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('operation.id', ondelete='CASCADE'), nullable=False)
    physician_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('physician.id', ondelete='RESTRICT'), nullable=False)
    surgical_role_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('cat_surgical_role.id', ondelete='RESTRICT'), nullable=False)

    operation: Mapped["Operation"] = relationship("Operation", back_populates="operation_physicians")
    physician: Mapped["Physician"] = relationship("Physician", back_populates="operation_physicians")
    surgical_role: Mapped["CatSurgicalRole"] = relationship("CatSurgicalRole", back_populates="operation_physicians")
