from extensions import db
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped,mapped_column,relationship
from sqlalchemy import ForeignKey, UniqueConstraint, text
import uuid

class OperationPatient(db.Model):
    __tablename__ = 'operation_patient'
    __table_args__ = (
        UniqueConstraint('operation_id', 'patient_id', name='uk_operation_patient_pair'),
    )

    # Atributes
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, server_default=text('gen_random_uuid()'))
    operation_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('operation.id', ondelete='CASCADE'), nullable=False)
    patient_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('patient.id', ondelete='RESTRICT'), nullable=False)

    # Relations
    operation: Mapped["Operation"] = relationship("Operation", back_populates="involves") # OperationPatient -> Operation 'operation'
    patient: Mapped["Patient"] = relationship("Patient", back_populates="participates") # OperationPatient -> Patient 'patient'
