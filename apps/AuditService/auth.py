"""Independent JWT validation compatible with BackendAuthService (PEF-DS01 / PEF-DS09).

Same contract as Catalog / Session, re-implemented so this service imports nothing from Auth:
- `Authorization: Bearer <access_token>` (no cookies, no identity headers);
- JWT signed with exactly JWT_ALGORITHM, claims sub / jti / type=access / exp;
- Redis `auth:jwt:{jti}` present, not revoked, same user and token type;
- user and institution active in PostgreSQL; tenant = user.institution_id; roles = effective
  user_role → role rows of that institution (never the `role` claim).

Audit access (scope applied in every query, never trusted to the client):
- it_admin       → every event of the caller's institution;
- spd_supervisor → operational events of the institution (sessions, instruments, kits, count events);
- anyone else    → 403, recorded as DENIED_GET_AUDIT_EVENT.
"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import wraps
from typing import Callable

import jwt
from flask import current_app, g, request
from redis.exceptions import RedisError
from sqlalchemy import text

from errors import ApiError

logger = logging.getLogger("audit.auth")

ACCESS_TOKEN_TYPE = "access"
ADMIN_ROLE_CODE = "it_admin"
SUPERVISOR_ROLE_CODE = "spd_supervisor"
AUDIT_ROLE_CODES = (ADMIN_ROLE_CODE, SUPERVISOR_ROLE_CODE)
REDIS_JWT_KEY = "auth:jwt:{jti}"
NIL_UUID = uuid.UUID(int=0)


@dataclass(frozen=True)
class Principal:
    user_id: uuid.UUID
    institution_id: uuid.UUID
    roles: frozenset[str]
    jti: str

    def has_role(self, code: str) -> bool:
        return code in self.roles

    @property
    def scope(self) -> str:
        """'admin' (it_admin, also when combined) or 'supervisor'."""
        return "admin" if self.has_role(ADMIN_ROLE_CODE) else "supervisor"


def current_principal() -> Principal:
    return g.principal


def _bearer_token() -> str:
    scheme, _, token = request.headers.get("Authorization", "").partition(" ")
    token = token.strip()
    if scheme.lower() != "bearer" or not token:
        raise ApiError.unauthorized("Bearer access token is required")
    return token


def _decode(token: str) -> dict:
    settings = current_app.config["AUDIT_SETTINGS"]
    try:
        claims = jwt.decode(token, settings.jwt_secret_key, algorithms=[settings.jwt_algorithm],
                            options={"require": ["exp", "sub", "jti", "type"]})
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
        raw = current_app.extensions["audit"]["redis"].get(REDIS_JWT_KEY.format(jti=jti))
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


def parse_uuid(value) -> uuid.UUID | None:
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError):
        return None


def authenticate() -> Principal:
    claims = _decode(_bearer_token())
    stored = _stored_session(claims["jti"])
    user_id = parse_uuid(claims["sub"])
    if user_id is None or parse_uuid(stored.get("user_id")) != user_id:
        raise ApiError.unauthorized("Session is no longer valid")
    with current_app.extensions["audit"]["database"].read() as connection:
        state = connection.execute(text(
            'SELECT u.active, u.institution_id, i.active AS institution_active FROM "user" u '
            "JOIN institution i ON i.id = u.institution_id WHERE u.id = :id"), {"id": user_id}).mappings().first()
        if not state or not state["active"] or not state["institution_active"]:
            raise ApiError.unauthorized("Session is no longer valid")
        institution_id = state["institution_id"]
        stored_institution = stored.get("institution_id")
        if stored_institution is not None and parse_uuid(stored_institution) != institution_id:
            raise ApiError.unauthorized("Session is no longer valid")
        roles = frozenset(connection.execute(text(
            "SELECT r.code FROM user_role ur JOIN role r ON r.id = ur.role_id "
            "WHERE ur.user_id = :id AND r.institution_id = :inst"), {"id": user_id, "inst": institution_id}).scalars())
    return Principal(user_id=user_id, institution_id=institution_id, roles=roles, jti=claims["jti"])


def record_denied(principal: Principal, resource_id: uuid.UUID | None) -> None:
    """DENIED_GET_AUDIT_EVENT in its own transaction; never turns the 403 into a 500."""
    try:
        with current_app.extensions["audit"]["database"].begin() as connection:
            connection.execute(text(
                "INSERT INTO access_audit (id, occurred_at, actor_type, actor_user_id, action, resource_type, "
                "resource_id, institution_id, outcome, correlation_id) VALUES (:id, :at, 'user', :actor, "
                f"'DENIED_{request.method.upper()}_AUDIT_EVENT', 'audit_event', :resource, :inst, 'denied', :corr)"),
                {"id": uuid.uuid4(), "at": datetime.now(timezone.utc), "actor": principal.user_id,
                 "resource": resource_id or NIL_UUID, "inst": principal.institution_id,
                 "corr": getattr(g, "correlation_id", None)})
    except Exception:  # noqa: BLE001
        logger.warning("could not record a denied audit request")


def require_audit_reader(view: Callable) -> Callable:
    @wraps(view)
    def wrapped(*args, **kwargs):
        principal = authenticate()
        g.principal = principal
        if not any(principal.has_role(code) for code in AUDIT_ROLE_CODES):
            record_denied(principal, parse_uuid(kwargs.get("event_id")))
            raise ApiError.forbidden()
        return view(*args, **kwargs)

    return wrapped
