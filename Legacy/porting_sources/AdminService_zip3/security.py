import json
import uuid
from dataclasses import dataclass, field
from functools import wraps

import jwt
import redis
from flask import g, request
from redis.exceptions import RedisError
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

import audit
from config import Config
from database import db
from errors import ApiError
from models import Institution, Role, User, UserRole


ADMIN = 'it_admin'
SUPERVISOR = 'spd_supervisor'
OPERATOR = 'station_operator'

_redis_client = None


def _redis():
    global _redis_client
    if _redis_client is None:
        _redis_client = redis.Redis.from_url(Config.REDIS_URL, decode_responses=True)
    return _redis_client


def set_redis_client(client):
    """Tests inject a fake store here."""
    global _redis_client
    _redis_client = client


@dataclass
class Actor:
    id: uuid.UUID
    name: str
    email: str
    institution_id: uuid.UUID
    role_codes: list = field(default_factory=list)

    @property
    def primary_role(self):
        for code in (ADMIN, SUPERVISOR, OPERATOR):
            if code in self.role_codes:
                return code
        return self.role_codes[0] if self.role_codes else None

    def has_any(self, *codes):
        return any(code in self.role_codes for code in codes)


def _bearer_token():
    header = request.headers.get('Authorization', '')
    if header.startswith('Bearer '):
        return header[7:].strip()
    return None


def _session_record(jti):
    try:
        raw = _redis().get(f'auth:jwt:{jti}')
    except RedisError as exc:
        raise ApiError(503, 'session_store_unavailable', 'Session store unavailable.') from exc
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def authenticate():
    """Return the Actor behind the request's access token or raise ApiError(401)."""
    if getattr(g, 'actor', None) is not None:
        return g.actor
    token = _bearer_token()
    if not token:
        raise ApiError(401, 'token_required', 'Bearer token is required.')
    try:
        claims = jwt.decode(token, Config.JWT_SECRET_KEY, algorithms=[Config.JWT_ALGORITHM])
    except jwt.InvalidTokenError as exc:
        raise ApiError(401, 'invalid_token', 'Invalid or expired token.') from exc
    if claims.get('type') != 'access' or not claims.get('jti'):
        raise ApiError(401, 'invalid_token', 'Invalid token type.')

    stored = _session_record(claims['jti'])
    if not stored or stored.get('revoked') or stored.get('token_type') != 'access':
        raise ApiError(401, 'token_revoked', 'Token is revoked or expired.')

    try:
        user_id = uuid.UUID(str(stored.get('user_id') or claims.get('sub')))
    except ValueError as exc:
        raise ApiError(401, 'invalid_token', 'Invalid token subject.') from exc

    try:
        row = db.execute(
            select(User, Institution.active)
            .join(Institution, Institution.id == User.institution_id)
            .where(User.id == user_id)
        ).first()
        if row is None or not row[0].active or not row[1]:
            raise ApiError(401, 'user_inactive', 'User is inactive.')
        user = row[0]
        role_codes = db.execute(
            select(Role.code).join(UserRole, UserRole.role_id == Role.id)
            .where(UserRole.user_id == user.id).order_by(Role.code)
        ).scalars().all()
    except SQLAlchemyError as exc:
        db.rollback()
        raise ApiError(503, 'database_unavailable', 'Database unavailable.') from exc

    g.actor = Actor(
        id=user.id,
        name=user.name,
        email=user.email,
        institution_id=user.institution_id,
        role_codes=list(role_codes),
    )
    return g.actor


def require_roles(*role_codes, resource_type='admin_api'):
    """Allow the view only to actors holding one of `role_codes`; denials are audited."""
    def decorator(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            actor = authenticate()
            if not actor.has_any(*role_codes):
                audit.record_denied(actor, resource_type, kwargs)
                raise ApiError(403, 'forbidden', 'Your role cannot perform this operation.')
            return view(*args, **kwargs)
        return wrapped
    return decorator
