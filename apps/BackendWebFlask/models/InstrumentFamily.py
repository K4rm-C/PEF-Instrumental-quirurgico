from extensions import db
from sqlalchemy.dialects.postgresql import UUID, TIMESTAMP, JSONB
from sqlalchemy.orm import Mapped,mapped_column,relationship
from sqlalchemy import ForeignKey, String, Text, Boolean
import uuid
from datetime import datetime, timezone

class InstrumentFamily(db.Model): # Missing String size
    __tablename__ = 'instrument_family'
    
    # Attributes
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    code: Mapped[str] = mapped_column(String(), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(), nullable=False)
    category_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('cat_instrument_category'), nullable=False)
    identify_text: Mapped[str] = mapped_column(Text, nullable=False)
    classify_text: Mapped[str] = mapped_column(Text, nullable=False)
    function_text: Mapped[str] = mapped_column(Text, nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False)
    
    # Relations
    category: Mapped["CatInstrumentCategory"] = relationship("CatInstrumentCategory", back_populates="families") # InstrumentFamily -> CatInstrumentCategory 'category'
    yolo_class: Mapped[list["ModelClass"]] = relationship("ModelClass", back_populates='family') # InstrumentFamily << ModelClass 'yolo_class'
    examples: Mapped[list["FamilyExample"]] = relationship("FamilyExample", back_populates="family") # InstrumentFamily << FamilyExample 'examples'
    units: Mapped[list["Instrument"]] = relationship("Instrument", back_populates="family") # InstrumentFamily << Instrument 'units'
    # InstrumentFamily << KitItem 'kit_line'