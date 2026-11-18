import logging
import ipaddress
import uuid
from datetime import datetime, timezone

from flask import g, request
from sqlalchemy.exc import SQLAlchemyError

from database import db
from errors import ApiError
from models import AccessAudit


logger = logging.getLogger(__name__)
NIL_UUID = uuid.UUID(int=0)


def correlation_id():
    if getattr(g, 'correlation_id', None) is None:
        incoming = (request.headers.get('X-Request-ID') or '').strip()
        g.correlation_id = incoming[:64] if incoming else uuid.uuid4().hex
    return g.correlation_id


def client_ip():
    forwarded = request.headers.get('X-Forwarded-For') or ''
    remote = request.remote_addr or ''
    for candidate in (forwarded.split(',')[0].strip(), remote.strip()):
        if not candidate:
            continue
        try:
            return str(ipaddress.ip_address(candidate))
        except ValueError:
            continue
    return None


def record(actor, action, resource_type, resource_id, *, outcome='success'):
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
        ip=client_ip(),
    ))


def record_denied(actor, resource_type, view_kwargs):
    target = NIL_UUID
    for value in (view_kwargs or {}).values():
        try:
            target = uuid.UUID(str(value))
            break
        except (ValueError, TypeError, AttributeError):
            continue
    action = f'{request.method}_{resource_type}'.upper()
    try:
        db.rollback()
        record(actor, f'DENIED_{action}', resource_type, target, outcome='denied')
        db.commit()
    except SQLAlchemyError:
        db.rollback()
        logger.exception('Could not persist denied supervisor access audit event')
        raise ApiError(503, 'audit_unavailable', 'Authorization audit could not be recorded.')
