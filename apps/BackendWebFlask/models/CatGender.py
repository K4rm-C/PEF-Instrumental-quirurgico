from extensions import db
from sqlalchemy.dialects.postgresql import UUID, TIMESTAMP, JSONB
from sqlalchemy.orm import Mapped,mapped_column,relationship
from sqlalchemy import ForeignKey, String, Text, Boolean
import uuid
from datetime import datetime, timezone

class CatGender(db.Model): # Missing String size
    __tablename__ = 'cat_gender'
    
    # Atributes
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    code: Mapped[str] = mapped_column(String(), nullable=False)
    name: Mapped[str] = mapped_column(String(), nullable=False)
    
    # Relations
    patients: Mapped[list["Patient"]] = relationship("Patient", back_populates='gender') # CatGender << Patient 'patients'