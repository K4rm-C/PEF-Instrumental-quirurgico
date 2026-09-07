from extensions import db
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy import ForeignKey, String, Text, Boolean
import uuid

class InstrumentFamily(db.Model):
    __tablename__ = 'instrument_family'

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    code: Mapped[str] = mapped_column(String(), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(), nullable=False)
    identify_text: Mapped[str] = mapped_column(Text(), nullable=True)
    classify_text: Mapped[str] = mapped_column(Text(), nullable=True)
    function_text: Mapped[str] = mapped_column(Text(), nullable=True)
    active: Mapped[bool] = mapped_column(Boolean(), nullable=False, default=True)

    category_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey('cat_instrument_category.id'), nullable=False)
    category: Mapped["CatInstrumentCategory"] = relationship("CatInstrumentCategory", back_populates="families")
    kit_items: Mapped[list["KitItem"]] = relationship("KitItem", back_populates="family")
    