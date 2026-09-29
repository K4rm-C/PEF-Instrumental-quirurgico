"""Reusable access-audit helper backed by the AccessAudit model (access_audit table).

Transaction policy:
- record_audit() only adds the AccessAudit row to the *current* session. The caller commits it
  together with the business change, so an operation and its audit row are atomic: if the
  audit insert fails, the whole operation is rolled back and reported as failed (never a silent
  success without audit).
- record_denied() is for rejected attempts (e.g. cross-institution access). Nothing else is
  pending at that point, so it commits on its own; a failure there is logged and never masks
  the original denial response.
"""
import uuid

from flask import current_app, has_request_context, request

from extensions import db
from models.AccessAudit import AccessAudit


def _as_uuid(value):
    if value is None or isinstance(value, uuid.UUID):
        return value
    try:
        return uuid.UUID(str(value))
    except ValueError:
        return None


def _request_context():
    if not has_request_context():
        return None, None
    correlation_id = (request.headers.get('X-Request-ID') or request.headers.get('X-Correlation-ID') or '')[:64] or None
    return correlation_id, request.remote_addr


def build_audit(action, resource_type, resource_id, actor=None, institution_id=None, outcome='success',
                actor_client_id=None):
    """Return an unsaved AccessAudit. actor is the verified-user dict (id, institution_id)."""
    actor = actor or {}
    correlation_id, ip = _request_context()
    client_id = _as_uuid(actor_client_id)
    return AccessAudit(
        actor_type='client' if client_id else 'user',
        actor_user_id=None if client_id else _as_uuid(actor.get('id')),
        actor_client_id=client_id,
        action=action,
        resource_type=resource_type,
        resource_id=_as_uuid(resource_id) or uuid.UUID(int=0),
        institution_id=_as_uuid(institution_id) or _as_uuid(actor.get('institution_id')),
        outcome=outcome,
        correlation_id=correlation_id,
        ip=ip,
    )


def record_audit(action, resource_type, resource_id, actor=None, **kwargs):
    """Stage an audit row in the caller's transaction (committed by the caller)."""
    entry = build_audit(action, resource_type, resource_id, actor=actor, **kwargs)
    db.session.add(entry)
    return entry


def record_denied(action, resource_type, resource_id, actor=None, **kwargs):
    """Persist a 'denied' audit row in its own transaction (best effort, logged on failure)."""
    try:
        db.session.rollback()
        db.session.add(build_audit(action, resource_type, resource_id, actor=actor, outcome='denied', **kwargs))
        db.session.commit()
    except Exception:  # noqa: BLE001 - auditing a denial must never raise over the denial itself
        db.session.rollback()
        current_app.logger.exception('Failed to record denied audit event %s', action)
