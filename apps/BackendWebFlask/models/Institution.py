from extensions import db
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy import ForeignKey, String, Boolean
import uuid

class Institution(db.Model):
    __tablename__ = 'institution'
    
    
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String, nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    
    parent_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('institution.id'), nullable=True) 
    parent: Mapped["Institution"] = relationship("Institution", remote_side=[id], back_populates="children")
    children: Mapped[list["Institution"]] = relationship("Institution", back_populates="parent")
