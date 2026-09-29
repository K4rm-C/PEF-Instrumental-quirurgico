from extensions import db
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy import String, UniqueConstraint, text
import uuid


class CatGender(db.Model):
    __tablename__ = 'cat_gender'
    __table_args__ = (
        UniqueConstraint('code', name='uk_cat_gender_code'),
    )

    # Attributes
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, server_default=text('gen_random_uuid()'))
    code: Mapped[str] = mapped_column(String(32), nullable=False)
    name: Mapped[str] = mapped_column(String(80), nullable=False)

    # Relations
    patients: Mapped[list["Patient"]] = relationship("Patient", back_populates='gender') # CatGender << Patient 'patients'
