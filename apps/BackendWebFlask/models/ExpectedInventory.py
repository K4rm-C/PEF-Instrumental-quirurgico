from extensions import db
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy import ForeignKey, SmallInteger, String, CheckConstraint, UniqueConstraint, text
import uuid

class ExpectedInventory(db.Model):
    __tablename__ = 'expected_inventory'
    __table_args__ = (
        UniqueConstraint('session_id', 'family_id', name='uk_expected_inventory_session_family'),
        CheckConstraint('expected_quantity >= 0', name='chk_expected_inventory_quantity'),
        CheckConstraint("source IN ('kit_snapshot', 'manual')", name='chk_expected_inventory_source'),
    )

    # Attributes
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, server_default=text('gen_random_uuid()'))
    expected_quantity: Mapped[int] = mapped_column(SmallInteger, nullable=False)  # smallint
    source: Mapped[str] = mapped_column(String(32), nullable=False, default='kit_snapshot', server_default=text("'kit_snapshot'"))  # kit_snapshot | manual

    # FKs
    family_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('instrument_family.id', ondelete='RESTRICT'), nullable=False)
    session_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('work_session.id', ondelete='CASCADE'), nullable=False)

    # Relations
    family: Mapped["InstrumentFamily"] = relationship("InstrumentFamily")
    session: Mapped["WorkSession"] = relationship("WorkSession", back_populates="expected_inventory")
