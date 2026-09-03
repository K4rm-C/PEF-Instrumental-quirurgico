from extensions import db
from sqlalchemy.dialects.postgresql import UUID, TIMESTAMP
from sqlalchemy.orm import Mapped,mapped_column,relationship
from sqlalchemy import ForeignKey, Boolean
import uuid
from datetime import datetime, timezone

class WorkSession(db.Model):
    __tablename__ = 'work_session'
    
    # Atributos
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    started_at: Mapped[TIMESTAMP] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    ended_at: Mapped[TIMESTAMP] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    status_id: Mapped[uuid.UUID]  = mapped_column(ForeignKey('cat_session_status.id'), nullable=False)
    user_id: Mapped[uuid.UUID]  = mapped_column(ForeignKey('user.id'), nullable=False)
    operation_id: Mapped[uuid.UUID]  = mapped_column(ForeignKey('operation.id'), nullable=False)
    station_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('capture_station.id'), nullable=False)
    kit_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('kit.id'), nullable=False)
    atypical_session: Mapped[bool] = mapped_column(Boolean, nullable=False)
    extended_retention: Mapped[bool] = mapped_column(Boolean, nullable=False)
    retention_until: Mapped[TIMESTAMP] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    
    # Relaciones
    # WorkSession -> cat_session_status "status"
    # WorkSession -> user  "user"
    # WorkSession -> operation "operation"
    # WorkSession -> station "station"
    # WorkSession -> kit "kit"
    # WorkSession <- count_event "audit"
    # WorkSession <- discrepancy "conflicts"
    # WorkSessionn <- expected_inventory "inventorySnapshot"