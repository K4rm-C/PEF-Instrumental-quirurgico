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

    instruments: Mapped[list["Instrument"]] = relationship("Instrument", back_populates="institution")
    kits: Mapped[list["Kit"]] = relationship("Kit", back_populates="institution")
    physician: Mapped[list["Physician"]] = relationship("Physician", back_populates="institution")
    roles: Mapped[list["Role"]] = relationship("Role", back_populates="institution")
    integration_clients: Mapped[list["IntegrationClient"]] = relationship("IntegrationClient", back_populates="institution")
    access_audits: Mapped[list["AccessAudit"]] = relationship("AccessAudit", back_populates="institution")