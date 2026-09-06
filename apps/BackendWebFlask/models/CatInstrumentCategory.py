from extensions import db
from sqlalchemy.dialects.postgresql import UUID, TIMESTAMP, JSONB
from sqlalchemy.orm import Mapped,mapped_column,relationship
from sqlalchemy import ForeignKey, String, Text, Boolean
import uuid
from datetime import datetime, timezone

class CatInstrumentCategory(db.Model): # Missing String size
    __tablename__ = 'cat_instrument_category'
    
    # Atributes
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    code: Mapped[str] = mapped_column(String(), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(), nullable=False)
    
    # Relations
    families: Mapped[list["InstrumentFamily"]] = relationship("InstrumentFamily", back_populates='category') # CatInstrumentCategory << InstrumentFamily 'families'