from extensions import db
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy import ForeignKey, SmallInteger
import uuid

class KitItem(db.Model):
    __tablename__='kit_item'

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    quantity: Mapped[int] = mapped_column(SmallInteger(), nullable=False)
    kit_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey('kit.id'), nullable=False)
    family_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey('instrument_family.id'), nullable=False)

    kit: Mapped["Kit"] = relationship("Kit", back_populates="items")
    family: Mapped["InstrumentFamily"] = relationship("InstrumentFamily", back_populates="kit_items") 
       