from extensions import db
from sqlalchemy.dialects.postgresql import UUID, TIMESTAMP
from sqlalchemy.orm import Mapped,mapped_column,relationship
from sqlalchemy import ForeignKey, Text, Boolean, SMALLINT, CheckConstraint, func, text
import uuid
from datetime import datetime

class Discrepancy(db.Model):
    __tablename__ = 'discrepancy'
    __table_args__ = (
        CheckConstraint('resolved = FALSE OR resolved_at IS NOT NULL', name='chk_discrepancy_resolved_at'),
        CheckConstraint(
            '(expected_quantity IS NULL OR expected_quantity >= 0) '
            'AND (detected_quantity IS NULL OR detected_quantity >= 0)',
            name='chk_discrepancy_quantities',
        ),
    )

    # Attributes
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, server_default=text('gen_random_uuid()'))
    description: Mapped[str] = mapped_column(Text, nullable=False)
    resolved: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=text('false'))
    resolved_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    reason_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey('cat_discrepancy_reason.id', ondelete='SET NULL'), nullable=True)
    session_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('work_session.id', ondelete='CASCADE'), nullable=False)
    origin_event_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey('count_event.id', ondelete='SET NULL'), nullable=True)
    expected_quantity: Mapped[int | None] = mapped_column(SMALLINT, nullable=True)
    detected_quantity: Mapped[int | None] = mapped_column(SMALLINT, nullable=True)
    family_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey('instrument_family.id', ondelete='SET NULL'), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())

    # Relaciones
    reason: Mapped["CatDiscrepancyReason | None"] = relationship("CatDiscrepancyReason", back_populates="discrepancies") # Discrepancy -> CatDiscrepancyReason 'Reason'
    origin: Mapped["CountEvent | None"] = relationship("CountEvent", back_populates="discrepancies") # Discrepancy -> CountEvent 'origin'
    session: Mapped["WorkSession"] = relationship("WorkSession", back_populates="conflicts") # Discrepancy -> WorkSession 'session'
