from extensions import db
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy import ForeignKey
import uuid

class PhysicianSpecialty(db.Model):
    __tablename__ = 'physician_specialty'

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    physician_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey('physician.id'), nullable=False)
    specialty_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey('cat_specialty.id'), nullable=False)

    physician: Mapped["Physician"] = relationship("Physician", back_populates="physicians_specialties")
    specialty: Mapped["CatSpecialty"] = relationship("CatSpecialty", back_populates="physicians_specialties")