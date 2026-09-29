from extensions import db
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy import ForeignKey, SmallInteger, CheckConstraint, UniqueConstraint, text
import uuid

class KitItem(db.Model):
    __tablename__='kit_item'
    __table_args__ = (
        UniqueConstraint('kit_id', 'family_id', name='uk_kit_item_kit_family'),
        CheckConstraint('quantity > 0', name='chk_kit_item_quantity'),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, server_default=text('gen_random_uuid()'))
    quantity: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    kit_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('kit.id', ondelete='CASCADE'), nullable=False)
    family_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('instrument_family.id', ondelete='RESTRICT'), nullable=False)

    kit: Mapped["Kit"] = relationship("Kit", back_populates="items")
    family: Mapped["InstrumentFamily"] = relationship("InstrumentFamily", back_populates="kit_items")
