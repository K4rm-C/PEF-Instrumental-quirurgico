from extensions import db
from sqlalchemy.dialects.postgresql import UUID, TIMESTAMP, JSONB
from sqlalchemy.orm import Mapped,mapped_column,relationship
from sqlalchemy import ForeignKey, String, Text, Boolean
import uuid
from datetime import datetime, timezone

class CatUsageContext(db.Model):
    __tablename__ = 'cat_usage_context'
    
    # Attributes
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    code: Mapped[str] = mapped_column(String(), nullable=False)
    name: Mapped[str] = mapped_column(String(), nullable=False)
    
    # Relations
    usages: Mapped[list["InstrumentUsage"]] = relationship("InstrumentUsage", back_populates="context") # CatUsageContext << InstrumentUsage 'usages'