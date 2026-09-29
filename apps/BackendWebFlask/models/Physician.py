from extensions import db
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy import ForeignKey, String, Boolean, text
import uuid

class Physician(db.Model):
    __tablename__ = 'physician'

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, server_default=text('gen_random_uuid()'))
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default=text('true'))
    institution_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('institution.id', ondelete='RESTRICT'), nullable=False)
    institution: Mapped["Institution"] = relationship("Institution", back_populates="physician")

    physicians_specialties: Mapped[list["PhysicianSpecialty"]] = relationship("PhysicianSpecialty", back_populates="physician", cascade="all, delete-orphan")
    operation_physicians: Mapped[list["OperationPhysician"]] = relationship("OperationPhysician", back_populates="physician")
