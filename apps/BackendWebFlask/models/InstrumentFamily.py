from extensions import db
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy import ForeignKey, String, Text, Boolean, UniqueConstraint, text
import uuid

class InstrumentFamily(db.Model):
    __tablename__ = 'instrument_family'
    __table_args__ = (
        UniqueConstraint('code', name='uk_instrument_family_code'),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, server_default=text('gen_random_uuid()'))
    code: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    identify_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    classify_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    function_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default=text('true'))

    category_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('cat_instrument_category.id', ondelete='RESTRICT'), nullable=False)
    category: Mapped["CatInstrumentCategory"] = relationship("CatInstrumentCategory", back_populates="families")
    kit_items: Mapped[list["KitItem"]] = relationship("KitItem", back_populates="family")
    model_classes: Mapped[list["ModelClass"]] = relationship("ModelClass", back_populates="family")
