import uuid
from datetime import datetime, timezone

from flask import g, request
from sqlalchemy.exc import SQLAlchemyError

from database import db
from models import AccessAudit


NIL_UUID = uuid.UUID(int=0)


def _client_ip():
    forwarded = request.headers.get('X-Forwarded-For') or request.remote_addr or ''
    value = forwarded.split(',')[0].strip()
    return value or None


def correlation_id():
    if getattr(g, 'correlation_id', None) is None:
        incoming = (request.headers.get('X-Request-ID') or '').strip()
        g.correlation_id = incoming[:64] if incoming else uuid.uuid4().hex
    return g.correlation_id


def record(action, resource_type, resource_id, *, actor=None, outcome='success'):
    actor = actor or g.actor
    db.add(AccessAudit(
        id=uuid.uuid4(),
        occurred_at=datetime.now(timezone.utc),
        actor_type='user',
        actor_user_id=actor.id,
        action=action[:64],
        resource_type=resource_type[:64],
        resource_id=resource_id or NIL_UUID,
        institution_id=actor.institution_id,
        outcome=outcome,
        correlation_id=correlation_id(),
        ip=_client_ip(),
    ))


def record_update(entity, resource_type, resource_id, *, was_active=None, is_active=None,
                  changed=True):
    """UPDATE_<ENTITY> when fields changed, plus DEACTIVATE_/ACTIVATE_ on a status flip."""
    if changed:
        record(f'UPDATE_{entity}', resource_type, resource_id)
    if was_active is not None and is_active is not None and was_active != is_active:
        record(f'{"ACTIVATE" if is_active else "DEACTIVATE"}_{entity}', resource_type, resource_id)


def record_denied(actor, resource_type, view_kwargs):
    """Persist a denied attempt in its own transaction (the request itself is aborted)."""
    target = NIL_UUID
    for value in (view_kwargs or {}).values():
        try:
            target = uuid.UUID(str(value))
            break
        except ValueError:
            continue
    action = f'{request.method}_{resource_type}'.upper() if resource_type else 'ACCESS'
    try:
        db.rollback()
        record(f'DENIED_{action}', resource_type or 'admin_api', target, actor=actor, outcome='denied')
        db.commit()
    except SQLAlchemyError:
        db.rollback()
