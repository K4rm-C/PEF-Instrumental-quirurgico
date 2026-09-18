from extensions import db
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy import String
import uuid

class CatSpecialty(db.Model):
    __tablename__ = 'cat_specialty'

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    code: Mapped[str] = mapped_column(String(), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(), nullable=False)

    physicians_specialties: Mapped[list["PhysicianSpecialty"]] = relationship("PhysicianSpecialty", back_populates="specialty")
    