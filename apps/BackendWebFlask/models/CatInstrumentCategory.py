from extensions import db
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy import String, UniqueConstraint, text
import uuid


class CatInstrumentCategory(db.Model):
    __tablename__ = 'cat_instrument_category'
    __table_args__ = (
        UniqueConstraint('code', name='uk_cat_instrument_category_code'),
    )

    # Attributes
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, server_default=text('gen_random_uuid()'))
    code: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)

    # Relations
    families: Mapped[list["InstrumentFamily"]] = relationship("InstrumentFamily", back_populates="category")
