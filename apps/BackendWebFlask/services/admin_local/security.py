"""Reuse the identity verified by BackendAuthService through the portal.

No second HTTP call and no duplicated JWT/Redis implementation. _current_user
already verifies the cookie with AuthService (including session revocation).
"""
import uuid
from dataclasses import dataclass, field
from functools import wraps
from flask import g
from . import audit
from .errors import ApiError

ADMIN = 'it_admin'
SUPERVISOR = 'spd_supervisor'
OPERATOR = 'station_operator'


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


def authenticate():
    actor = getattr(g, 'actor', None)
    if actor is not None:
        return actor
    # Import lazily: routes.py itself imports admin_routes.py during initialization.
    from controllers.routes import _current_user
    user = _current_user()
    if not user:
        raise ApiError(401, 'token_required', 'Sign in to continue.')
    try:
        actor = Actor(
            id=uuid.UUID(str(user['id'])),
            institution_id=uuid.UUID(str(user['institution_id'])),
            name=user.get('name', ''),
            email=user.get('email', ''),
            role_codes=[role['code'] for role in (user.get('roles') or []) if role.get('code')],
        )
    except (TypeError, KeyError, ValueError) as exc:
        raise ApiError(401, 'invalid_token', 'Authenticated user details are invalid.') from exc
    g.actor = actor
    return actor


def require_roles(*role_codes, resource_type='admin'):
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
