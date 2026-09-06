from extensions import db
from sqlalchemy.dialects.postgresql import UUID, TIMESTAMP, JSONB
from sqlalchemy.orm import Mapped,mapped_column,relationship
from sqlalchemy import ForeignKey, String, Text, Boolean
import uuid
from datetime import datetime, timezone

class Patient(db.Model): # Missing String size
    __tablename__ = 'patient'
    
    # Atributes
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    display_name: Mapped[str] = mapped_column(String(), nullable=False)
    birth_date: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False)
    gender_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('cat_gender.id'), nullable=False)
    institution_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('institution.id'), nullable=False)
    
    # Relations
    gender: Mapped["CatGender"] = relationship("CatGender", back_populates="patients") # Patient -> CatGender 'gender'
    # Patient -> Institution 'institution'
    participates: Mapped[list["OperationPatient"]] = relationship("OperationPatient", back_populates="patient") # Patient << OperationPatiient 'participates'