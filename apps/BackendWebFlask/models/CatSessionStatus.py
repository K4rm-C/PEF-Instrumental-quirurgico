from extensions import db
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped,mapped_column,relationship
from sqlalchemy import ForeignKey, String
import uuid

class CatSessionStatus(db.Model): # Falta settear tamaño de strings
    __tablename__ = 'cat_session_status'
    
    # Atributos
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    code: Mapped[str] = mapped_column(String(), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(), nullable=False)
    
    # Relaciones
    sessions: Mapped[list["WorkSessions"]] = relationship("WorkSessions", back_populates="status") # CatSessionStatus << WorkSessions 'sessions'