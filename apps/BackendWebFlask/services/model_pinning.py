"""MS-02 / P1: the YOLO model a vision session uses is fixed once (work_session.yolo_model_id) and never changes.

When: inside the Operator's Start Session transaction (row locked FOR UPDATE) if the session uses vision and a
model is active; otherwise at its first bind (first use of inference), again under the row lock. Once set, it is
never reassigned (migration 011 also refuses it in the database). Each pin is audited (`session.model_pinned`).

Historical sessions: closed sessions without a model are left as they are. An in-progress session started before
MS-02 gets the model that is active at its first bind (the one it was actually using). If a session HAD a pin
(audit row) and the reference is gone, nothing is re-pinned: the bind fails with a clear error.
"""
from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import select, text

from extensions import db
from models.AccessAudit import AccessAudit
from models.Operation import Operation
from models.WorkSession import WorkSession
from models.YoloModel import YoloModel

PIN_ACTION = "session.model_pinned"


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class ModelPinError(Exception):
    def __init__(self, code: str, message: str, status_code: int = 409):
        super().__init__(message)
        self.code, self.message, self.status_code = code, message, status_code


def active_model() -> YoloModel | None:
    return db.session.scalar(select(YoloModel).where(YoloModel.active.is_(True)))


def _institution_of(work: WorkSession):
    operation = db.session.get(Operation, work.operation_id) if work.operation_id else None
    return operation.institution_id if operation else None


def pin_in_transaction(work: WorkSession, *, user_id, reason: str, ip: str | None = None) -> YoloModel | None:
    """Set the session's model to the active one if it has none (caller holds the row lock and commits)."""
    if work.yolo_model_id is not None:
        return db.session.get(YoloModel, work.yolo_model_id)
    model = active_model()
    if model is None:
        return None
    work.yolo_model_id = model.id
    # access_audit requires a user or client actor (chk_access_audit_actor_pair): the pin is made on behalf of
    # the session's assigned Operator when no other user triggers it (first bind).
    db.session.add(AccessAudit(
        occurred_at=utc_now(), actor_type="user", actor_user_id=user_id or work.user_id,
        action=PIN_ACTION, resource_type="work_session", resource_id=work.id,
        institution_id=_institution_of(work), outcome="success", ip=ip,
    ))
    return model


def was_pinned_before(session_id: UUID) -> bool:
    return db.session.execute(text(
        "SELECT 1 FROM access_audit WHERE action = :a AND resource_type = 'work_session' AND resource_id = :s LIMIT 1"
    ), {"a": PIN_ACTION, "s": session_id}).first() is not None


def model_for_session(session_id: UUID, *, user_id=None) -> YoloModel:
    """The session's pinned model; pins it now (first bind) if the session never had one."""
    work = db.session.get(WorkSession, session_id)
    if work is None:
        raise ModelPinError("session_not_found", "Session not found.", 404)
    if work.yolo_model_id is not None:
        model = db.session.get(YoloModel, work.yolo_model_id)
        if model is None:
            raise ModelPinError("pinned_model_missing", "The vision model of this session no longer exists.", 409)
        return model
    if was_pinned_before(session_id):
        raise ModelPinError("pinned_model_missing",
                            "The vision model of this session no longer exists; it is not replaced automatically.", 409)
    # First use of inference: lock the row so concurrent binds pin the same model exactly once.
    work = db.session.scalar(select(WorkSession).where(WorkSession.id == session_id).with_for_update())
    model = pin_in_transaction(work, user_id=user_id, reason="first_bind")
    if model is None:
        db.session.rollback()
        raise ModelPinError("no_active_model", "No vision model is active. Ask Administration to activate one.", 409)
    db.session.commit()
    return model
