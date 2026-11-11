from extensions import db
from sqlalchemy.dialects.postgresql import UUID, TIMESTAMP, JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy import ForeignKey, SMALLINT
import uuid
from datetime import datetime, timezone


class CountEvent(db.Model):
    __tablename__ = 'count_event'

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    event_type_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('cat_event_type.id'), nullable=False)
    client_event_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    occurred_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    session_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('work_session.id'), nullable=False)
    user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey('user.id'), nullable=True)
    expected_quantity: Mapped[int | None] = mapped_column(SMALLINT, nullable=True)
    detected_quantity: Mapped[int | None] = mapped_column(SMALLINT, nullable=True)
    family_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey('instrument_family.id'), nullable=True)

    session: Mapped["WorkSession"] = relationship("WorkSession", back_populates="audit")
    user: Mapped["User | None"] = relationship("User")
    reviews: Mapped[list["HumanCorrection"]] = relationship("HumanCorrection", back_populates="review")
    discrepancies: Mapped[list["Discrepancy"]] = relationship("Discrepancy", back_populates="origin")
    event_type: Mapped["CatEventType"] = relationship("CatEventType", back_populates="events")
