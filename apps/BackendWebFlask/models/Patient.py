from extensions import db
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped,mapped_column,relationship
from sqlalchemy import ForeignKey, String, Boolean, Date, text
import uuid
from datetime import date

class Patient(db.Model):
    __tablename__ = 'patient'

    # Atributes
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, server_default=text('gen_random_uuid()'))
    display_name: Mapped[str] = mapped_column(String(160), nullable=False)
    birth_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default=text('true'))
    gender_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey('cat_gender.id', ondelete='SET NULL'), nullable=True)
    institution_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('institution.id', ondelete='RESTRICT'), nullable=False)

    # Relations
    gender: Mapped["CatGender | None"] = relationship("CatGender", back_populates="patients") # Patient -> CatGender 'gender'
    # Patient -> Institution 'institution'
    participates: Mapped[list["OperationPatient"]] = relationship("OperationPatient", back_populates="patient") # Patient << OperationPatiient 'participates'
    privacy_requests: Mapped[list["PrivacyRequest"]] = relationship("PrivacyRequest", back_populates="subject_patient")
