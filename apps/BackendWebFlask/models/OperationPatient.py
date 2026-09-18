from extensions import db
from sqlalchemy.dialects.postgresql import UUID, TIMESTAMP, JSONB
from sqlalchemy.orm import Mapped,mapped_column,relationship
from sqlalchemy import ForeignKey, String, Text, Boolean
import uuid
from datetime import datetime, timezone

class OperationPatient(db.Model):
    __tablename__ = 'operation_patient'
    
    # Atributes
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    operation_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('operation.id'), nullable=False)
    patient_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('patient.id'), nullable=False)
    
    # Relations
    operation: Mapped["Operation"] = relationship("Operation", back_populates="involves") # OperationPatient -> Operation 'operation'
    patient: Mapped["Patient"] = relationship("Patient", back_populates="participates") # OperationPatient -> Patient 'patient'