from extensions import db
from sqlalchemy.dialects.postgresql import UUID, TIMESTAMP, JSONB
from sqlalchemy.orm import Mapped,mapped_column,relationship
from sqlalchemy import ForeignKey, SMALLINT, CheckConstraint, UniqueConstraint, func, text
import uuid
from datetime import datetime

class CountEvent(db.Model):
    __tablename__ = 'count_event'
    __table_args__ = (
        UniqueConstraint('client_event_id', name='uk_count_event_client_event'),
        CheckConstraint(
            '(expected_quantity IS NULL OR expected_quantity >= 0) '
            'AND (detected_quantity IS NULL OR detected_quantity >= 0)',
            name='chk_count_event_quantities',
        ),
    )

    # Attributes
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, server_default=text('gen_random_uuid()'))
    event_type_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('cat_event_type.id', ondelete='RESTRICT'), nullable=False)
    client_event_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    occurred_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False, server_default=func.now())
    session_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('work_session.id', ondelete='CASCADE'), nullable=False)
    user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey('user.id', ondelete='SET NULL'), nullable=True)
    expected_quantity: Mapped[int | None] = mapped_column(SMALLINT, nullable=True)
    detected_quantity: Mapped[int | None] = mapped_column(SMALLINT, nullable=True)
    family_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey('instrument_family.id', ondelete='SET NULL'), nullable=True)

    # Relations
    session: Mapped["WorkSession"] = relationship("WorkSession", back_populates="audit") # CountEvent -> WorkSession 'session'
    user: Mapped["User | None"] = relationship("User") # CountEvent -> User 'user'
    reviews: Mapped[list["HumanCorrection"]] = relationship("HumanCorrection", back_populates="review") # CountEvent << HumanCorrection 'reviews'
    discrepancies: Mapped[list["Discrepancy"]] = relationship("Discrepancy", back_populates="origin") # CountEvent << Discrepancy 'discrepancies'
    event_type: Mapped["CatEventType"] = relationship("CatEventType", back_populates="events")
