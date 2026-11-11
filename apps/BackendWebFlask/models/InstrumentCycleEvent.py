from extensions import db
from sqlalchemy.dialects.postgresql import UUID, TIMESTAMP
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy import ForeignKey, Text
import uuid
from datetime import datetime, timezone


class InstrumentCycleEvent(db.Model):
    __tablename__ = 'instrument_cycle_event'

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    instrument_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('instrument.id'), nullable=False)
    cycle_status_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('cat_instrument_cycle_status.id'), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    session_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey('work_session.id'), nullable=True)
    operation_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey('operation.id'), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    instrument: Mapped["Instrument"] = relationship("Instrument", back_populates="history")
    cycle_status: Mapped["CatInstrumentCycleStatus"] = relationship(
        "CatInstrumentCycleStatus", back_populates="instrument_cycle_events"
    )
    session: Mapped["WorkSession | None"] = relationship("WorkSession", back_populates="cycle")
    operation: Mapped["Operation | None"] = relationship("Operation", back_populates="cycle")
