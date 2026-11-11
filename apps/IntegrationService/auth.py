"""JWT validation for people (desktop / Android / portal users) and machine clients (PEF-DS08).

Users: same contract as every PEF service — `type = access`, Redis `auth:jwt:{jti}` present and not revoked,
user and institution active, roles of the user's own institution from PostgreSQL (never the claim).

Machine clients: tokens issued by AuthService `POST /api/v1/auth/token` (client credentials) with
`type = client_access`, `sub` = integration_client.id. Checked here: signature/algorithm/expiry, Redis entry
(revocation), the client row active and of an active institution, and the scopes stored in PostgreSQL right
now (a scope removed by Administration stops working at once, whatever the token says). A user token is never
accepted where only clients are allowed and vice versa (each endpoint states who may call it).
"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass, field
from functools import wraps
from typing import Callable, Iterable

import jwt
from flask import current_app, g, request
from redis.exceptions import RedisError
from sqlalchemy import text

from errors import ApiError

logger = logging.getLogger("integration.auth")

USER_TOKEN = "access"
CLIENT_TOKEN = "client_access"
ADMIN, SUPERVISOR, OPERATOR = "it_admin", "spd_supervisor", "station_operator"
NIL_UUID = uuid.UUID(int=0)


@dataclass(frozen=True)
class Principal:
    kind: str                        # user | client
    id: uuid.UUID
    institution_id: uuid.UUID
    roles: frozenset[str] = frozenset()
    scopes: frozenset[str] = frozenset()
    jti: str = ""
    token: str = field(default="", repr=False)    # forwarded to owner services (users only)
    client_code: str = ""

    @property
    def is_user(self) -> bool:
        return self.kind == "user"

    def has_role(self, code: str) -> bool:
        return self.is_user and code in self.roles

    def has_scope(self, scope: str) -> bool:
        return self.kind == "client" and scope in self.scopes

    # audit columns
    @property
    def actor(self) -> dict:
        return {"actor_type": self.kind, "actor_user_id": self.id if self.is_user else None,
                "actor_client_id": None if self.is_user else self.id}


def current_principal() -> Principal:
    return g.principal


def _bearer_token() -> str:
    scheme, _, token = request.headers.get("Authorization", "").partition(" ")
    token = token.strip()
    if scheme.lower() != "bearer" or not token:
        raise ApiError.unauthorized("Bearer access token is required")
    return token


def _decode(token: str) -> dict:
    settings = current_app.config["INTEGRATION_SETTINGS"]
    try:
        claims = jwt.decode(token, settings.jwt_secret_key, algorithms=[settings.jwt_algorithm],
                            options={"require": ["exp", "sub", "jti", "type"]})
    except jwt.ExpiredSignatureError:
        raise ApiError.unauthorized("Access token has expired") from None
    except jwt.InvalidTokenError:
        raise ApiError.unauthorized("Invalid access token") from None
    if claims.get("type") not in (USER_TOKEN, CLIENT_TOKEN):
        raise ApiError.unauthorized("Token is not an access token")
    if not isinstance(claims.get("sub"), str) or not isinstance(claims.get("jti"), str) or not claims["jti"]:
        raise ApiError.unauthorized("Invalid access token")
    return claims


def _stored(jti: str, token_type: str) -> dict:
    try:
        raw = current_app.extensions["integration"]["redis"].get(f"auth:jwt:{jti}")
    except RedisError:
        logger.error("redis unavailable while validating token")
        raise ApiError.dependency_unavailable("redis") from None
    try:
        stored = json.loads(raw) if raw else None
    except ValueError:
        stored = None
    if not isinstance(stored, dict) or stored.get("revoked") or stored.get("token_type") != token_type:
        raise ApiError.unauthorized("Session is no longer valid")
    return stored


def parse_uuid(value) -> uuid.UUID | None:
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError):
        return None


def authenticate() -> Principal:
    token = _bearer_token()
    claims = _decode(token)
    stored = _stored(claims["jti"], claims["type"])
    subject = parse_uuid(claims["sub"])
    database = current_app.extensions["integration"]["database"]
    if claims["type"] == USER_TOKEN:
        if subject is None or parse_uuid(stored.get("user_id")) != subject:
            raise ApiError.unauthorized("Session is no longer valid")
        with database.connect() as connection:
            state = connection.execute(text(
                'SELECT u.active, u.institution_id, i.active AS institution_active FROM "user" u '
                "JOIN institution i ON i.id = u.institution_id WHERE u.id = :id"), {"id": subject}).mappings().first()
            if not state or not state["active"] or not state["institution_active"]:
                raise ApiError.unauthorized("Session is no longer valid")
            stored_institution = stored.get("institution_id")
            if stored_institution is not None and parse_uuid(stored_institution) != state["institution_id"]:
                raise ApiError.unauthorized("Session is no longer valid")
            roles = frozenset(connection.execute(text(
                "SELECT r.code FROM user_role ur JOIN role r ON r.id = ur.role_id "
                "WHERE ur.user_id = :id AND r.institution_id = :inst"),
                {"id": subject, "inst": state["institution_id"]}).scalars())
        return Principal("user", subject, state["institution_id"], roles=roles, jti=claims["jti"], token=token)
    # machine client
    if subject is None or parse_uuid(stored.get("client_id")) != subject:
        raise ApiError.unauthorized("Session is no longer valid")
    with database.connect() as connection:
        row = connection.execute(text(
            "SELECT c.client_id, c.scopes, c.active, c.institution_id, i.active AS institution_active "
            "FROM integration_client c JOIN institution i ON i.id = c.institution_id WHERE c.id = :id"),
            {"id": subject}).mappings().first()
    if not row or not row["active"] or not row["institution_active"]:
        raise ApiError.unauthorized("Client is no longer authorized")
    if parse_uuid(stored.get("institution_id")) != row["institution_id"]:
        raise ApiError.unauthorized("Client is no longer authorized")
    granted = {s for s in (row["scopes"] or []) if isinstance(s, str)}
    claimed = set(str(claims.get("scope") or "").split())
    return Principal("client", subject, row["institution_id"], scopes=frozenset(granted & claimed), jti=claims["jti"],
                     client_code=row["client_id"])


def require(*, roles: Iterable[str] = (), scopes: Iterable[str] = (), any_scope_prefix: str = "") -> Callable:
    """Authenticate; a user needs one of `roles`, a client one of `scopes` (or any scope with the prefix).
    Who is not listed gets 403 (an empty `roles` means: no user may call it; same for clients)."""
    roles, scopes = tuple(roles), tuple(scopes)

    def decorator(view: Callable) -> Callable:
        @wraps(view)
        def wrapped(*args, **kwargs):
            principal = authenticate()
            g.principal = principal
            if principal.is_user:
                allowed = any(principal.has_role(code) for code in roles)
            else:
                allowed = any(principal.has_scope(s) for s in scopes) or bool(
                    any_scope_prefix and any(s.startswith(any_scope_prefix) for s in principal.scopes))
            if not allowed:
                logger.info("forbidden: %s=%s path=%s", principal.kind, principal.id, request.path)
                raise ApiError.forbidden()
            return view(*args, **kwargs)
        return wrapped
    return decorator
