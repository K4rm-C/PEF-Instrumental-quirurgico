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


SUPERVISOR = 'spd_supervisor'
_redis_client = None


def _redis():
    global _redis_client
    if _redis_client is None:
        _redis_client = redis.Redis.from_url(Config.REDIS_URL, decode_responses=True)
    return _redis_client


def set_redis_client(client):
    """Inject a Redis-compatible client in tests."""
    global _redis_client
    _redis_client = client


@dataclass
class Actor:
    id: uuid.UUID
    name: str
    email: str
    institution_id: uuid.UUID
    role_codes: list[str] = field(default_factory=list)

    def has_role(self, code):
        return code in self.role_codes


def _bearer_token():
    header = request.headers.get('Authorization', '')
    if header.startswith('Bearer '):
        token = header[7:].strip()
        return token or None
    return None


def _session_record(jti):
    try:
        raw = _redis().get(f'auth:jwt:{jti}')
    except RedisError as exc:
        raise ApiError(503, 'session_store_unavailable', 'Session store unavailable.') from exc
    if not raw:
        return None
    try:
        value = json.loads(raw)
    except (TypeError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def authenticate():
    if getattr(g, 'actor', None) is not None:
        return g.actor
    token = _bearer_token()
    if not token:
        raise ApiError(401, 'token_required', 'Bearer access token is required.')
    try:
        claims = jwt.decode(token, Config.JWT_SECRET_KEY, algorithms=[Config.JWT_ALGORITHM])
    except jwt.InvalidTokenError as exc:
        raise ApiError(401, 'invalid_token', 'Invalid or expired token.') from exc
    if claims.get('type') != 'access' or not claims.get('jti') or not claims.get('sub'):
        raise ApiError(401, 'invalid_token', 'An access token with subject and token ID is required.')

    stored = _session_record(claims['jti'])
    if (
        not stored
        or str(stored.get('jti') or '') != str(claims['jti'])
        or stored.get('revoked') is True
        or stored.get('token_type') != 'access'
        or str(stored.get('user_id') or '') != str(claims['sub'])
    ):
        raise ApiError(401, 'token_revoked', 'Token session is missing, mismatched, or revoked.')

    try:
        user_id = uuid.UUID(str(claims['sub']))
    except (TypeError, ValueError) as exc:
        raise ApiError(401, 'invalid_token', 'Invalid token subject.') from exc

    try:
        row = db.execute(
            select(User, Institution.active)
            .join(Institution, Institution.id == User.institution_id)
            .where(User.id == user_id)
        ).first()
        if row is None or not row[0].active or not row[1]:
            raise ApiError(401, 'user_inactive', 'User or institution is inactive.')
        user = row[0]
        if stored.get('institution_id') and str(stored['institution_id']) != str(user.institution_id):
            raise ApiError(401, 'invalid_token', 'Token institution does not match the user.')
        role_codes = db.execute(
            select(Role.code)
            .join(UserRole, UserRole.role_id == Role.id)
            .where(
                UserRole.user_id == user.id,
                Role.institution_id == user.institution_id,
            )
            .order_by(Role.code)
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


def require_supervisor(resource_type='supervisor_api'):
    def decorator(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            actor = authenticate()
            if not actor.has_role(SUPERVISOR):
                audit.record_denied(actor, resource_type, kwargs)
                raise ApiError(403, 'forbidden', 'SPD supervisor role is required.')
            return view(*args, **kwargs)
        return wrapped
    return decorator
