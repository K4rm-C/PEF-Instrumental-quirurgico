from extensions import db
from sqlalchemy.dialects.postgresql import UUID, TIMESTAMP
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy import String, Text, Boolean, CheckConstraint
import uuid
from datetime import datetime


class ResourceIdentifier(db.Model):
    __tablename__ = 'resource_identifier'
    __table_args__ = (
        CheckConstraint(
            "resource_type IN ('institution', 'patient', 'physician', 'operation', 'operating_room', 'instrument')",
            name='chk_resource_identifier_type',
        ),
        CheckConstraint(
            "use_code IN ('usual', 'official', 'temp', 'secondary', 'old')",
            name='chk_resource_identifier_use_code',
        ),
        CheckConstraint(
            'period_end IS NULL OR period_end >= period_start',
            name='chk_resource_identifier_period',
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    resource_type: Mapped[str] = mapped_column(String(32), nullable=False)
    resource_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    system: Mapped[str] = mapped_column(Text, nullable=False)
    value: Mapped[str] = mapped_column(String(255), nullable=False)
    use_code: Mapped[str] = mapped_column(String(16), nullable=False, default='official')
    period_start: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    period_end: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    first_seen_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)