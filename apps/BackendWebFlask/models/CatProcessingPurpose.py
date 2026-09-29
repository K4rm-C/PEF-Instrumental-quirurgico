from extensions import db
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy import String, Boolean, CheckConstraint, UniqueConstraint, text
import uuid


class CatProcessingPurpose(db.Model):
    __tablename__ = 'cat_processing_purpose'
    __table_args__ = (
        UniqueConstraint('code', name='uk_cat_processing_purpose_code'),
        CheckConstraint(
            "code IN ('quality_ops', 'model_improvement', 'external_sharing')",
            name='chk_cat_processing_purpose_code',
        ),
    )

    # Attributes
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, server_default=text('gen_random_uuid()'))
    code: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default=text('true'))
