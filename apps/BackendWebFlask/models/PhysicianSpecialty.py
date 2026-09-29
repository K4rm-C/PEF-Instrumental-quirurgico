from extensions import db
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy import ForeignKey, UniqueConstraint, text
import uuid

class PhysicianSpecialty(db.Model):
    __tablename__ = 'physician_specialty'
    __table_args__ = (
        UniqueConstraint('physician_id', 'specialty_id', name='uk_physician_specialty_pair'),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, server_default=text('gen_random_uuid()'))
    physician_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('physician.id', ondelete='CASCADE'), nullable=False)
    specialty_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('cat_specialty.id', ondelete='RESTRICT'), nullable=False)

    physician: Mapped["Physician"] = relationship("Physician", back_populates="physicians_specialties")
    specialty: Mapped["CatSpecialty"] = relationship("CatSpecialty", back_populates="physicians_specialties")
