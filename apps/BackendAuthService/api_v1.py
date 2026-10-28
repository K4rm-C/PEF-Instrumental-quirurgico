"""Versioned Auth API for external clients (PEF-DS09 §1.3.2) and DS09 health checks.

Android (JSON) / Desktop (XML) clients receive tokens in the response body and send
``Authorization: Bearer <access_token>`` to the microservices. The legacy unversioned
routes in main.py (cookies, used by BackendWebFlask) are untouched; both layers share the
same internal rules from main.py: authenticate_credentials, verify_token, _issue_pair_for_user,
rotate_refresh, revoke_token.

``core`` is the main module; its attributes are read at call time so tests can patch
``main.engine`` / ``main.token_store``.
"""

from __future__ import annotations

import logging
from functools import wraps
from types import ModuleType
from typing import Any, Callable

from flask import Blueprint, Flask, g, request
from redis.exceptions import RedisError
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, InterfaceError, OperationalError, SQLAlchemyError
from werkzeug.exceptions import HTTPException

from pathlib import Path

from flask import Response

from api_format import (
	ApiError,
	configure_api_logging,
	error_response,
	parse_string_fields,
	register_correlation,
	render,
)

from identity_admin import register_identity_admin

logger = logging.getLogger("auth.api")
SERVICE_DIR = Path(__file__).resolve().parent

API_PREFIX = "/api/v1/auth"


def register_api(app: Flask, core: ModuleType) -> None:
	configure_api_logging()
	api_bp = Blueprint("api_v1_auth", __name__, url_prefix=API_PREFIX)
	health_bp = Blueprint("health_ds09", __name__, url_prefix="/health")
	for blueprint in (api_bp, health_bp):
		register_correlation(blueprint)
		_register_blueprint_errors(blueprint)

	# --- Identity helpers ---------------------------------------------------------------

	def load_identity(user_id: str) -> dict[str, Any] | None:
		"""Current profile from PostgreSQL; roles limited to the user's own institution."""
		with core.engine.connect() as connection:
			user = connection.execute(
				text(
					"""
					SELECT u.id, u.name, u.email, u.active, u.ui_preferences, u.institution_id,
					       i.active AS institution_active
					FROM "user" u
					JOIN institution i ON i.id = u.institution_id
					WHERE u.id = CAST(:id AS uuid)
					"""
				),
				{"id": str(user_id)},
			).mappings().first()
			if not user:
				return None
			roles = connection.execute(
				text(
					"""
					SELECT r.code
					FROM user_role ur
					JOIN role r ON r.id = ur.role_id
					WHERE ur.user_id = CAST(:id AS uuid)
					  AND r.institution_id = CAST(:institution_id AS uuid)
					ORDER BY r.code
					"""
				),
				{"id": str(user_id), "institution_id": str(user["institution_id"])},
			).scalars().all()
		return {**dict(user), "roles": list(roles)}

	def user_summary(identity: dict[str, Any]) -> dict[str, Any]:
		"""DS09 `user` object (login response and GET /me)."""
		return {
			"id": str(identity["id"]),
			"name": identity["name"],
			"email": identity["email"],
			"institution_id": str(identity["institution_id"]),
			"ui_preferences": core.normalize_ui_preferences(identity["ui_preferences"]),
			"roles": identity["roles"],
		}

	def primary_role(codes: list[str]) -> str:
		return core._primary_role_code([{"code": code} for code in codes])

	def checked_token(raw_token: str, token_type: str) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
		"""Shared token rules + tenant coherence. Returns (claims, stored session, identity)."""
		try:
			claims, stored = core.verify_token(raw_token, token_type)
		except core.TokenRejected as exc:
			raise ApiError(401, exc.message) from None
		identity = load_identity(stored["user_id"])
		if not identity or not identity["active"] or not identity["institution_active"]:
			raise ApiError(401, "Token is revoked or user is inactive")
		# Institution recorded at login must still be the user's institution (PostgreSQL wins).
		stored_institution = stored.get("institution_id")
		if stored_institution is not None and str(stored_institution) != str(identity["institution_id"]):
			raise ApiError(401, "Token is revoked or user is inactive")
		g.api_user_id = str(identity["id"])
		return claims, stored, identity

	def require_bearer(view: Callable) -> Callable:
		@wraps(view)
		def wrapped(*args, **kwargs):
			scheme, _, token = request.headers.get("Authorization", "").partition(" ")
			token = token.strip()
			if scheme.lower() != "bearer" or not token:
				raise ApiError(401, "Bearer access token is required")
			g.api_claims, g.api_stored, g.api_identity = checked_token(token, "access")
			return view(*args, **kwargs)

		return wrapped

	def token_body(access: str, refresh: str, access_age: int) -> dict[str, Any]:
		return {
			"access_token": access,
			"refresh_token": refresh,
			"token_type": "Bearer",
			"expires_in": access_age,
		}

	# --- Endpoints ----------------------------------------------------------------------

	@api_bp.post("/login")
	def login():
		body = parse_string_fields("login_request", ("email", "password"), required=("email", "password"))
		email = body["email"].strip().lower()
		try:
			user, _ = core.authenticate_credentials(email, body["password"], core.client_ip_from_request())
		except core.LoginRejected as exc:
			raise ApiError(exc.status, exc.message, error=exc.reason) from None
		identity = load_identity(user["id"])
		try:
			access, refresh, access_age, _ = core._issue_pair_for_user(
				str(user["id"]), str(user["institution_id"]), primary_role(identity["roles"]),
			)
		except PermissionError:
			raise ApiError(401, "Session expired; sign in again.") from None
		g.api_user_id = str(user["id"])
		logger.info("api login user=%s", user["id"])
		return render({**token_body(access, refresh, access_age), "user": user_summary(identity)}, root="login_response")

	@api_bp.post("/refresh")
	def refresh():
		body = parse_string_fields("refresh_request", ("refresh_token",), required=("refresh_token",))
		claims, stored, identity = checked_token(body["refresh_token"], "refresh")
		role_code = primary_role(identity["roles"]) if identity["roles"] else (claims.get("role") or "default")
		try:
			access, new_refresh, access_age, _ = core.rotate_refresh(
				claims, str(identity["id"]), str(identity["institution_id"]), role_code,
				# An explicit client refresh is user activity (no silent heartbeat in the API).
				touch_activity=True,
			)
		except core.RefreshDenied as exc:
			raise ApiError(401, exc.message) from None
		return render(token_body(access, new_refresh, access_age), root="refresh_response")

	@api_bp.post("/logout")
	@require_bearer
	def logout():
		"""Ends the API session: revokes the Bearer access token AND its refresh token.

		Both are validated before anything is revoked, so a failed logout changes nothing.
		"""
		body = parse_string_fields("logout_request", ("refresh_token",), required=("refresh_token",))
		user_id = str(g.api_identity["id"])
		# Same rules as /refresh: signature/algorithm/expiry, type=refresh, Redis session
		# present and not revoked, active user.
		try:
			refresh_claims, refresh_stored = core.verify_token(body["refresh_token"], "refresh")
		except core.TokenRejected as exc:
			raise ApiError(401, f"refresh_token: {exc.message}") from None
		if str(refresh_stored["user_id"]) != user_id:
			# Never revoke (or reveal anything about) another user's session.
			raise ApiError(403, "refresh_token does not belong to the authenticated user")
		core.revoke_token(g.api_claims["jti"])
		core.revoke_token(refresh_claims["jti"])
		core.record_sign_in_event(user_id, g.api_identity["institution_id"], "LOGOUT")
		logger.info("api logout user=%s", user_id)
		return "", 204

	@api_bp.get("/me")
	@require_bearer
	def me():
		return render(user_summary(g.api_identity), root="user")

	@api_bp.get("/permissions")
	@require_bearer
	def permissions():
		identity = g.api_identity
		return render(
			{
				"user_id": str(identity["id"]),
				"institution_id": str(identity["institution_id"]),
				"roles": identity["roles"],
			},
			root="permissions",
		)

	# --- Health (DS09 §1.3.7: only the dependencies Auth uses) ---------------------------

	def check_database() -> str:
		try:
			with core.engine.connect() as connection:
				connection.execute(text("SELECT 1"))
			return "ok"
		except SQLAlchemyError as exc:
			logger.warning("health: database check failed (%s)", type(exc).__name__)
			return "error"

	def check_redis() -> str:
		try:
			return "ok" if core.token_store.ping() else "error"
		except RedisError as exc:
			logger.warning("health: redis check failed (%s)", type(exc).__name__)
			return "error"

	def health(ok_status: str, failed_status: str, checks: dict[str, str]):
		ok = all(value == "ok" for value in checks.values())
		payload = {"status": ok_status if ok else failed_status, "checks": checks, "correlation_id": g.correlation_id}
		return render(payload, root="health", status=200 if ok else 503)

	@health_bp.get("/live")
	def live():
		return health("live", "live", {})

	@health_bp.get("/database")
	def database():
		return health("ok", "unavailable", {"database": check_database()})

	@health_bp.get("/redis")
	def redis_health():
		return health("ok", "unavailable", {"redis": check_redis()})

	@health_bp.get("/ready")
	def ready():
		return health("ready", "not_ready", {"database": check_database(), "redis": check_redis()})

	# --- Identity administration (users, roles, passwords) + contract documents -----------

	register_identity_admin(api_bp, core, require_bearer)

	@api_bp.get("/openapi.yaml")
	def openapi_document():
		return Response((SERVICE_DIR / "openapi.yaml").read_bytes(), mimetype="application/yaml")

	@api_bp.get("/schemas/identity.xsd")
	def identity_schema():
		return Response((SERVICE_DIR / "schemas" / "identity.xsd").read_bytes(), mimetype="application/xml")

	@api_bp.get("/docs")
	def swagger_ui():
		html = """<!doctype html><html lang="en"><head><meta charset="utf-8"><title>PEF Auth / Identity API</title>
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/swagger-ui-dist@5.17.14/swagger-ui.css"></head>
<body><div id="swagger-ui"></div>
<script src="https://cdn.jsdelivr.net/npm/swagger-ui-dist@5.17.14/swagger-ui-bundle.js"></script>
<script>window.ui = SwaggerUIBundle({url: "openapi.yaml", dom_id: "#swagger-ui", persistAuthorization: true});</script>
</body></html>"""
		return Response(html, mimetype="text/html")

	app.register_blueprint(api_bp)
	app.register_blueprint(health_bp)
	_register_unmatched_api_errors(app)


def _register_blueprint_errors(blueprint: Blueprint) -> None:
	"""DS09 envelope for errors raised inside the versioned API / health views only."""

	@blueprint.errorhandler(ApiError)
	def _api_error(exc: ApiError):
		return error_response(exc)

	@blueprint.errorhandler(HTTPException)
	def _http_error(exc: HTTPException):
		return error_response(ApiError(exc.code or 500))

	@blueprint.errorhandler(RedisError)
	def _redis_error(exc: RedisError):
		logger.error("redis unavailable: %s", type(exc).__name__)
		return error_response(ApiError.dependency_unavailable("redis"))

	@blueprint.errorhandler(SQLAlchemyError)
	def _database_error(exc: SQLAlchemyError):
		if isinstance(exc, (OperationalError, InterfaceError)) or (
			isinstance(exc, DBAPIError) and exc.connection_invalidated
		):
			logger.error("database unavailable: %s", type(exc).__name__)
			return error_response(ApiError.dependency_unavailable("database"))
		logger.exception("database error")
		return error_response(ApiError(500))

	@blueprint.errorhandler(Exception)
	def _unexpected(exc: Exception):
		logger.exception("unhandled error")
		return error_response(ApiError(500))


def _register_unmatched_api_errors(app: Flask) -> None:
	"""404/405 under /api/v1/auth use the DS09 envelope; legacy paths keep Flask defaults."""

	def handler(exc: HTTPException):
		if request.path.startswith(API_PREFIX + "/") or request.path == API_PREFIX:
			return error_response(ApiError(exc.code or 500))
		return exc

	app.register_error_handler(404, handler)
	app.register_error_handler(405, handler)
