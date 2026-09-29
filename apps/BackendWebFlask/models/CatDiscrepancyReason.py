from extensions import db
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy import String, UniqueConstraint, text
import uuid


class CatDiscrepancyReason(db.Model):
    __tablename__ = 'cat_discrepancy_reason'
    __table_args__ = (
        UniqueConstraint('code', name='uk_cat_discrepancy_reason_code'),
    )

    # Attributes
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, server_default=text('gen_random_uuid()'))
    code: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(160), nullable=False)

    # Relations
    discrepancies: Mapped[list["Discrepancy"]] = relationship("Discrepancy", back_populates="reason") # CatDiscrepancyReason << Discrepancy 'Discrepancies'
