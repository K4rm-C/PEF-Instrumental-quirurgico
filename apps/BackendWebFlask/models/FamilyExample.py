from extensions import db
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped,mapped_column,relationship
from sqlalchemy import ForeignKey, Text, SMALLINT, text
import uuid

class FamilyExample(db.Model):
    __tablename__ = 'family_example'

    # Attributes
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, server_default=text('gen_random_uuid()'))
    family_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('instrument_family.id', ondelete='CASCADE'), nullable=False)
    media_asset_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey('media_asset.id', ondelete='SET NULL'), nullable=True)
    example_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    sort_order: Mapped[int] = mapped_column(SMALLINT, nullable=False, default=0, server_default=text('0'))

    # Relations
    family: Mapped["InstrumentFamily"] = relationship("InstrumentFamily") # FamilyExample -> InstrumentFamily 'family'
    media_asset: Mapped["MediaAsset | None"] = relationship("MediaAsset") # FamilyExample -> MediaAsset 'media_asset'
