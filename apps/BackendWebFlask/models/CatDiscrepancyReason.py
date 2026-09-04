from extensions import db
from sqlalchemy.dialects.postgresql import UUID, TIMESTAMP
from sqlalchemy.orm import Mapped,mapped_column,relationship
from sqlalchemy import ForeignKey, String
import uuid
from datetime import datetime, timezone

class CatDiscrepancyReason(db.Model): # String size missing
    __tablename__ = 'cat_discrepancy_reason'
    
    # Atributos
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    code: Mapped[str] = mapped_column(String(), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(), nullable=True)
    
    # Relaciones
    discrepancies: Mapped[list["Discrepancy"]] = relationship("Discrepancy", back_populates="reason") # CatDiscrepancyReason << Discrepancy 'Discrepancies'