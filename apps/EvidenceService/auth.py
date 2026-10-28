"""Independent JWT validation compatible with BackendAuthService (PEF-DS01 / PEF-DS09).

Re-implemented on purpose (no imports from Auth or other services):

- ``Authorization: Bearer <access_token>`` (cookies are never read);
- JWT signed with JWT_SECRET_KEY and decoded with EXACTLY JWT_ALGORITHM; required claims ``sub``,
  ``jti``, ``type`` (= access), ``exp``;
- Redis ``auth:jwt:{jti}`` → JSON ``{jti, user_id, institution_id, token_type, revoked, exp}``: must
  exist, not be revoked, be an access token of the same user;
- PostgreSQL: user and institution active, institution equal to the one recorded by Auth;
  roles = effective ``user_role`` → ``role`` rows of the user's institution (never the JWT claim).

Phase A has no clinical endpoint yet; ``require_auth`` and ``Principal`` are ready for Phase B
(roles ``station_operator`` / ``spd_supervisor``; ``it_admin`` gets 403 on clinical evidence).
"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass
from functools import wraps
from typing import Callable

import jwt
from flask import current_app, g, request
from redis.exceptions import RedisError

from errors import ApiError
from repositories import identity_repository as repo

logger = logging.getLogger("evidence.auth")

ACCESS_TOKEN_TYPE = "access"
OPERATOR_ROLE_CODE = "station_operator"
SUPERVISOR_ROLE_CODE = "spd_supervisor"
EVIDENCE_ROLE_CODES = (OPERATOR_ROLE_CODE, SUPERVISOR_ROLE_CODE)
REDIS_JWT_KEY = "auth:jwt:{jti}"


@dataclass(frozen=True)
class Principal:
    user_id: uuid.UUID
    institution_id: uuid.UUID
    roles: frozenset[str]
    jti: str

    def has_role(self, code: str) -> bool:
        return code in self.roles


def current_principal() -> Principal:
    return g.principal


def _services() -> dict:
    return current_app.extensions["evidence_service"]


def _bearer_token() -> str:
    header = request.headers.get("Authorization", "")
    scheme, _, token = header.partition(" ")
    token = token.strip()
    if scheme.lower() != "bearer" or not token:
        raise ApiError.unauthorized("Bearer access token is required")
    return token


def _decode(token: str) -> dict:
    settings = current_app.config["EVIDENCE_SETTINGS"]
    try:
        claims = jwt.decode(
            token,
            settings.jwt_secret_key,
            algorithms=[settings.jwt_algorithm],   # exactly one algorithm: never "none", never another HS*
            options={"require": ["exp", "sub", "jti", "type"]},
        )
    except jwt.ExpiredSignatureError:
        raise ApiError.unauthorized("Access token has expired") from None
    except jwt.InvalidTokenError:
        raise ApiError.unauthorized("Invalid access token") from None
    if claims.get("type") != ACCESS_TOKEN_TYPE:
        raise ApiError.unauthorized("Token is not an access token")
    if not isinstance(claims.get("sub"), str) or not isinstance(claims.get("jti"), str) or not claims["jti"]:
        raise ApiError.unauthorized("Invalid access token")
    return claims


def _stored_session(jti: str) -> dict:
    try:
        raw = _services()["redis"].get(REDIS_JWT_KEY.format(jti=jti))
    except RedisError:
        logger.error("redis unavailable while validating token")
        raise ApiError.dependency_unavailable("redis") from None
    if not raw:
        raise ApiError.unauthorized("Session is no longer valid")
    try:
        stored = json.loads(raw)
    except ValueError:
        raise ApiError.unauthorized("Session is no longer valid") from None
    if not isinstance(stored, dict) or stored.get("revoked"):
        raise ApiError.unauthorized("Session is no longer valid")
    if stored.get("token_type") != ACCESS_TOKEN_TYPE:
        raise ApiError.unauthorized("Token is not an access token")
    return stored


def _parse_uuid(value) -> uuid.UUID | None:
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError):
        return None


def authenticate() -> Principal:
    claims = _decode(_bearer_token())
    stored = _stored_session(claims["jti"])

    user_id = _parse_uuid(claims["sub"])
    if user_id is None or _parse_uuid(stored.get("user_id")) != user_id:
        raise ApiError.unauthorized("Session is no longer valid")

    with _services()["database"].connect() as connection:
        state = repo.get_user_auth_state(connection, user_id)
        if not state or not state["active"] or not state["institution_active"]:
            raise ApiError.unauthorized("Session is no longer valid")
        institution_id = state["institution_id"]
        # Auth records the tenant at login; a mismatch means the user was moved since.
        stored_institution = stored.get("institution_id")
        if stored_institution is not None and _parse_uuid(stored_institution) != institution_id:
            raise ApiError.unauthorized("Session is no longer valid")
        roles = frozenset(repo.list_role_codes(connection, user_id, institution_id))

    return Principal(user_id=user_id, institution_id=institution_id, roles=roles, jti=claims["jti"])


def require_auth(*required_roles: str) -> Callable:
    """Authenticate the request; when roles are given, the user needs at least one of them."""

    def decorator(view: Callable) -> Callable:
        @wraps(view)
        def wrapped(*args, **kwargs):
            principal = authenticate()
            g.principal = principal
            if required_roles and not any(principal.has_role(code) for code in required_roles):
                raise ApiError.forbidden()
            return view(*args, **kwargs)

        return wrapped

    return decorator
