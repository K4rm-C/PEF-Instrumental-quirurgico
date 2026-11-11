from extensions import db
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy import String
import uuid

class CatInstrumentCategory(db.Model):
    __tablename__ = 'cat_instrument_category'

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    code: Mapped[str] = mapped_column(String(), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(), nullable=False)

    families: Mapped[list["InstrumentFamily"]] = relationship("InstrumentFamily", back_populates="category")
    
