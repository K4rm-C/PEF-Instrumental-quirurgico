from extensions import db
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy import ForeignKey, Text, SmallInteger
import uuid

class FamilyExample(db.Model):
    __tablename__ = 'family_example'

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    family_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('instrument_family.id'), nullable=False)
    media_asset_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('media_asset.id'), nullable=False)
    example_text: Mapped[str] = mapped_column(Text, nullable=False) 
    sort_order: Mapped[int] = mapped_column(SmallInteger, nullable=False) 

    # Relaciones
    family: Mapped["InstrumentFamily"] = relationship("InstrumentFamily", back_populates="family_examples")
    media_asset: Mapped["MediaAsset"] = relationship("MediaAsset", back_populates="family_examples")