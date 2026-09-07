from extensions import db
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy import ForeignKey, SmallInteger
import uuid

class ExpectedInventory(db.Model):
    __tablename__ = 'expected_inventory'

    # Attributes
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    expected_quantity: Mapped[int] = mapped_column(SmallInteger, nullable=False)  # smallint
    
    # FKs
    family_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('instrument_family.id'), nullable=False)
    session_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('work_session.id'), nullable=False)

    # Relations
    family: Mapped["InstrumentFamily"] = relationship("InstrumentFamily")
    session: Mapped["WorkSession"] = relationship("WorkSession", back_populates="expected_inventory")