from extensions import db
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy import String, UniqueConstraint, text
import uuid


class CatSpecialty(db.Model):
    __tablename__ = 'cat_specialty'
    __table_args__ = (
        UniqueConstraint('code', name='uk_cat_specialty_code'),
    )

    # Attributes
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, server_default=text('gen_random_uuid()'))
    code: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(160), nullable=False)

    # Relations
    physicians_specialties: Mapped[list["PhysicianSpecialty"]] = relationship("PhysicianSpecialty", back_populates="specialty")
