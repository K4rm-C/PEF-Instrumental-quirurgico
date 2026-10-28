from __future__ import annotations

import json
import os
import re
import sys
import uuid
from datetime import datetime, timedelta, timezone
from functools import wraps
from typing import Any, Callable

import jwt
from dotenv import load_dotenv
from flask import Flask, g, jsonify, request
from redis.exceptions import RedisError
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.engine import Engine
from werkzeug.security import check_password_hash, generate_password_hash

from api_v1 import register_api
from token_store import RedisTokenStore, build_token_store


load_dotenv()


def build_database_url() -> str:
	database_url = os.getenv("DATABASE_URL")
	if database_url:
		return database_url

	return (
		"postgresql+psycopg://"
		f"{os.getenv('DB_USER', 'postgres')}:{os.getenv('DB_PASSWORD', '')}@"
		f"{os.getenv('DB_HOST', 'localhost')}:{os.getenv('DB_PORT', '5432')}/"
		f"{os.getenv('DB_NAME', 'pecausas')}"
	)


def utc_now() -> datetime:
	return datetime.now(timezone.utc)


def seconds_from_env(name: str, default: int) -> int:
	try:
		return max(1, int(os.getenv(name, str(default))))
	except ValueError:
		return default


app = Flask(__name__)
allowed_origins = {
	origin.strip().rstrip("/")
	for origin in os.getenv("AUTH_ALLOWED_ORIGINS", "").split(",")
	if origin.strip()
}
app.config.update(
	JWT_SECRET_KEY=os.getenv("JWT_SECRET_KEY", "change-this-secret"),
	JWT_ALGORITHM=os.getenv("JWT_ALGORITHM", "HS256"),
	# Defaults kept for admin / unknown roles; OP/SPD use ROLE_TOKEN_POLICY below.
	ACCESS_TOKEN_MINUTES=seconds_from_env("ACCESS_TOKEN_MINUTES", 15),
	REFRESH_TOKEN_HOURS=seconds_from_env("REFRESH_TOKEN_HOURS", 8),
	# Legacy env name still accepted (days → hours) if REFRESH_TOKEN_HOURS unset.
	REFRESH_TOKEN_DAYS=seconds_from_env("REFRESH_TOKEN_DAYS", 0),
	RESET_TOKEN_MINUTES=seconds_from_env("RESET_TOKEN_MINUTES", 30),
	SPD_IDLE_TIMEOUT_MINUTES=seconds_from_env("SPD_IDLE_TIMEOUT_MINUTES", 30),
	SPD_MAX_SESSION_HOURS=seconds_from_env("SPD_MAX_SESSION_HOURS", 12),
	OP_ACCESS_TOKEN_MINUTES=seconds_from_env("OP_ACCESS_TOKEN_MINUTES", 15),
	OP_REFRESH_TOKEN_HOURS=seconds_from_env("OP_REFRESH_TOKEN_HOURS", 24),
	OP_MAX_SESSION_HOURS=seconds_from_env("OP_MAX_SESSION_HOURS", 24),
	SPD_ACCESS_TOKEN_MINUTES=seconds_from_env("SPD_ACCESS_TOKEN_MINUTES", 15),
	SPD_REFRESH_TOKEN_HOURS=seconds_from_env("SPD_REFRESH_TOKEN_HOURS", 8),
	RETURN_RESET_TOKEN=os.getenv("AUTH_RETURN_RESET_TOKEN", "true").lower() == "true",
	# Unauthenticated self-registration skips every Administration rule (roles, audit, active
	# institution); accounts are created by an administrator (POST /api/v1/auth/users).
	ENABLE_PUBLIC_REGISTER=os.getenv("AUTH_ENABLE_PUBLIC_REGISTER", "false").lower() == "true",
	COOKIE_DOMAIN=os.getenv("AUTH_COOKIE_DOMAIN") or None,
	COOKIE_SECURE=os.getenv("AUTH_COOKIE_SECURE", "true").lower() == "true",
	COOKIE_SAMESITE=os.getenv("AUTH_COOKIE_SAMESITE", "None"),
)

if app.config["JWT_SECRET_KEY"] == "change-this-secret" and not app.debug:
	raise RuntimeError("JWT_SECRET_KEY must be configured outside debug mode")

# Tokens are signed with a shared secret. Only one algorithm is ever accepted on decode:
# exactly JWT_ALGORITHM (never the whole HS* family, never "none"). Same rule as CatalogService.
SUPPORTED_JWT_ALGORITHMS = {"HS256", "HS384", "HS512"}
if app.config["JWT_ALGORITHM"] not in SUPPORTED_JWT_ALGORITHMS:
	raise RuntimeError(f"JWT_ALGORITHM must be one of {sorted(SUPPORTED_JWT_ALGORITHMS)}")

engine: Engine = create_engine(build_database_url(), pool_pre_ping=True, future=True)
token_store: RedisTokenStore = build_token_store()


def token_from_request(token_type: str | None) -> str | None:
	header = request.headers.get("Authorization", "")
	if header.startswith("Bearer "):
		return header[7:]
	if token_type == "refresh":
		return request.cookies.get("refresh_token")
	return request.cookies.get("access_token")


def _primary_role_code(role_rows) -> str:
	codes = {row["code"] if isinstance(row, dict) else row.code for row in (role_rows or [])}
	# Prefer operator keep-alive policy when both somehow present.
	if "station_operator" in codes:
		return "station_operator"
	if "spd_supervisor" in codes:
		return "spd_supervisor"
	if "it_admin" in codes:
		return "it_admin"
	return "default"


def _token_policy(role_code: str) -> dict[str, int]:
	"""Access/refresh/idle/max lifetimes in seconds by role."""
	if role_code == "station_operator":
		return {
			"access": app.config["OP_ACCESS_TOKEN_MINUTES"] * 60,
			"refresh": app.config["OP_REFRESH_TOKEN_HOURS"] * 3600,
			"idle": 0,  # no idle while surgery active (enforced in refresh)
			"max_session": app.config["OP_MAX_SESSION_HOURS"] * 3600,
		}
	if role_code == "spd_supervisor":
		return {
			"access": app.config["SPD_ACCESS_TOKEN_MINUTES"] * 60,
			"refresh": app.config["SPD_REFRESH_TOKEN_HOURS"] * 3600,
			"idle": app.config["SPD_IDLE_TIMEOUT_MINUTES"] * 60,
			"max_session": app.config["SPD_MAX_SESSION_HOURS"] * 3600,
		}
	# Admin / default: SPD-like short access, longer refresh from REFRESH_TOKEN_HOURS
	refresh_hours = app.config["REFRESH_TOKEN_HOURS"]
	if app.config["REFRESH_TOKEN_DAYS"]:
		refresh_hours = max(refresh_hours, app.config["REFRESH_TOKEN_DAYS"] * 24)
	return {
		"access": app.config["ACCESS_TOKEN_MINUTES"] * 60,
		"refresh": refresh_hours * 3600,
		"idle": app.config["SPD_IDLE_TIMEOUT_MINUTES"] * 60,
		"max_session": app.config["SPD_MAX_SESSION_HOURS"] * 3600,
	}


def _operator_has_active_surgery(user_id: str) -> bool:
	"""True while OP has a work_session still in the counting flow (not yet sent to SPD review)."""
	try:
		with engine.connect() as connection:
			row = connection.execute(
				text(
					"""
					SELECT 1
					FROM work_session w
					JOIN cat_session_status s ON s.id = w.status_id
					WHERE w.user_id = CAST(:uid AS uuid)
					  AND s.code = 'in_progress'
					  AND w.ended_at IS NULL
					LIMIT 1
					"""
				),
				{"uid": user_id},
			).first()
		return bool(row)
	except SQLAlchemyError:
		return False


def set_auth_cookies(
	response,
	access_token: str,
	refresh_token: str,
	*,
	access_max_age: int,
	refresh_max_age: int,
):
	common = {
		"domain": app.config["COOKIE_DOMAIN"],
		"secure": app.config["COOKIE_SECURE"],
		"httponly": True,
		"samesite": app.config["COOKIE_SAMESITE"],
		"path": "/",
	}
	response.set_cookie(
		"access_token",
		access_token,
		max_age=access_max_age,
		**common,
	)
	response.set_cookie(
		"refresh_token",
		refresh_token,
		max_age=refresh_max_age,
		**common,
	)
	return response


def clear_auth_cookies(response):
	common = {
		"domain": app.config["COOKIE_DOMAIN"],
		"secure": app.config["COOKIE_SECURE"],
		"httponly": True,
		"samesite": app.config["COOKIE_SAMESITE"],
		"path": "/",
	}
	response.delete_cookie("access_token", **common)
	response.delete_cookie("refresh_token", **common)
	return response


@app.after_request
def add_cors_headers(response):
	origin = request.headers.get("Origin", "").rstrip("/")
	if origin in allowed_origins:
		response.headers["Access-Control-Allow-Origin"] = origin
		response.headers["Access-Control-Allow-Credentials"] = "true"
		response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
		response.headers["Access-Control-Allow-Headers"] = "Authorization, Content-Type"
		response.vary.add("Origin")
	return response


def error(message: str, status: int):
	return jsonify({"error": message}), status


def request_json() -> dict[str, Any]:
	payload = request.get_json(silent=True)
	return payload if isinstance(payload, dict) else {}


SUPPORTED_LOCALES = {"en", "es-MX"}
SUPPORTED_THEMES = {"light", "dark", "system"}


def normalize_ui_preferences(raw: Any) -> dict[str, Any]:
	prefs = dict(raw) if isinstance(raw, dict) else {}
	locale = prefs.get("locale") or prefs.get("language") or "en"
	if locale == "es":
		locale = "es-MX"
	if locale not in SUPPORTED_LOCALES:
		locale = "en"
	theme = prefs.get("theme") or "light"
	if theme not in SUPPORTED_THEMES:
		theme = "light"
	return {"locale": locale, "theme": theme}


def issue_token(
	user_id: str,
	token_type: str,
	lifetime: timedelta,
	institution_id: str | None = None,
	*,
	role_code: str = "default",
	auth_session_started: datetime | None = None,
	last_activity: datetime | None = None,
) -> str:
	issued_at = utc_now()
	token_id = str(uuid.uuid4())
	expires_at = issued_at + lifetime
	claims = {
		"sub": user_id,
		"jti": token_id,
		"type": token_type,
		"iat": issued_at,
		"exp": expires_at,
		"role": role_code,
		"sas": int((auth_session_started or issued_at).timestamp()),  # auth session started
		"la": int((last_activity or issued_at).timestamp()),  # last activity (idle)
	}
	token = jwt.encode(
		claims,
		app.config["JWT_SECRET_KEY"],
		algorithm=app.config["JWT_ALGORITHM"],
	)
	token_store.store_token(
		jti=token_id,
		user_id=user_id,
		institution_id=institution_id,
		token_type=token_type,
		expires_at=expires_at,
	)
	return token


def _issue_pair_for_user(
	user_id: str,
	institution_id: str | None,
	role_code: str,
	*,
	auth_session_started: datetime | None = None,
	last_activity: datetime | None = None,
	touch_activity: bool = True,
) -> tuple[str, str, int, int]:
	policy = _token_policy(role_code)
	now = utc_now()
	sas = auth_session_started or now
	# Absolute max session wall-clock from original login.
	if (now - sas).total_seconds() > policy["max_session"]:
		raise PermissionError("auth_session_expired")
	activity = now if touch_activity else (last_activity or sas)
	access = issue_token(
		user_id,
		"access",
		timedelta(seconds=policy["access"]),
		institution_id=institution_id,
		role_code=role_code,
		auth_session_started=sas,
		last_activity=activity,
	)
	refresh = issue_token(
		user_id,
		"refresh",
		timedelta(seconds=policy["refresh"]),
		institution_id=institution_id,
		role_code=role_code,
		auth_session_started=sas,
		last_activity=activity,
	)
	return access, refresh, policy["access"], policy["refresh"]


def revoke_token(jti: str) -> None:
	token_store.revoke_token(jti)


class TokenRejected(Exception):
	"""A token failed one of the shared validation rules (always HTTP 401)."""

	def __init__(self, message: str):
		super().__init__(message)
		self.message = message


def decode_token(raw_token: str) -> dict[str, Any]:
	"""Signature + expiry with exactly the configured algorithm; required claims present."""
	return jwt.decode(
		raw_token,
		app.config["JWT_SECRET_KEY"],
		algorithms=[app.config["JWT_ALGORITHM"]],
		options={"require": ["exp", "sub", "jti", "type"]},
	)


def verify_token(raw_token: str, token_type: str | None) -> tuple[dict[str, Any], dict[str, Any]]:
	"""Shared rules for legacy routes and /api/v1/auth: JWT, Redis session, active user.

	Returns (claims, stored Redis session). Raises TokenRejected; RedisError and
	SQLAlchemyError propagate so each caller keeps its own response format.
	"""
	try:
		claims = decode_token(raw_token)
	except jwt.InvalidTokenError:
		raise TokenRejected("Invalid or expired token") from None
	if token_type and claims.get("type") != token_type:
		raise TokenRejected("Invalid token type")
	stored = token_store.get_token(claims["jti"])
	if not stored or stored.get("revoked"):
		raise TokenRejected("Token is revoked or user is inactive")
	if stored.get("token_type") != claims.get("type"):
		raise TokenRejected("Invalid token type")
	if not stored.get("user_id") or str(stored["user_id"]) != str(claims["sub"]):
		raise TokenRejected("Invalid or expired token")
	with engine.connect() as connection:
		# Account and institution must still be active: deactivating either one ends every
		# session on the next request, even a token issued before the change.
		user = connection.execute(
			text(
				"SELECT active, (SELECT i.active FROM institution i WHERE i.id = u.institution_id) "
				"FROM \"user\" u WHERE u.id = :id"
			),
			{"id": stored["user_id"]},
		).first()
	if not user or not user[0] or not user[1]:
		raise TokenRejected("Token is revoked or user is inactive")
	return claims, stored


def token_required(token_type: str | None = "access"):
	def decorator(function: Callable):
		@wraps(function)
		def wrapped(*args, **kwargs):
			raw_token = token_from_request(token_type)
			if not raw_token:
				return error("Bearer token is required", 401)
			try:
				claims, stored = verify_token(raw_token, token_type)
			except TokenRejected as exc:
				return error(exc.message, 401)
			except RedisError:
				return error("Session store unavailable", 503)
			except SQLAlchemyError:
				return error("Invalid or expired token", 401)
			g.auth_claims = claims
			g.auth_user_id = stored["user_id"]
			g.auth_institution_id = stored.get("institution_id")
			return function(*args, **kwargs)

		return wrapped

	return decorator


@app.errorhandler(SQLAlchemyError)
def database_error(exception):
	app.logger.exception("Database error", exc_info=exception)
	return error("Database unavailable", 503)


@app.errorhandler(RedisError)
def redis_error(exception):
	app.logger.exception("Redis error", exc_info=exception)
	return error("Session store unavailable", 503)


@app.get("/health")
def health():
	checks = {"postgres": False, "redis": False}
	try:
		with engine.connect() as connection:
			connection.execute(text("SELECT 1"))
		checks["postgres"] = True
	except SQLAlchemyError:
		pass
	try:
		checks["redis"] = token_store.ping()
	except RedisError:
		pass
	status = 200 if all(checks.values()) else 503
	return jsonify({"ok": status == 200, "checks": checks}), status


@app.route("/login", methods=["OPTIONS"])
@app.route("/logout", methods=["OPTIONS"])
@app.route("/refresh", methods=["OPTIONS"])
@app.route("/verify", methods=["OPTIONS"])
def cors_preflight():
	return ("", 204)


@app.post("/register")
def register():
	if not app.config["ENABLE_PUBLIC_REGISTER"]:
		return error("Not found", 404)
	payload = request_json()
	name = str(payload.get("name", "")).strip()
	email = str(payload.get("email", "")).strip().lower()
	password = str(payload.get("password", ""))
	institution_id = payload.get("institution_id")
	if not name or not email or len(password) < 8 or not institution_id:
		return error("name, email, institution_id and a password of at least 8 characters are required", 400)
	try:
		user_id = str(uuid.uuid4())
		with engine.begin() as connection:
			connection.execute(
				text(
					"""
					INSERT INTO "user" (id, name, email, password_hash, institution_id)
					VALUES (CAST(:id AS uuid), :name, :email, :password_hash, CAST(:institution_id AS uuid))
					"""
				),
				{"id": user_id, "name": name, "email": email,
				 "password_hash": generate_password_hash(password), "institution_id": institution_id},
			)
		return jsonify({"id": user_id, "name": name, "email": email}), 201
	except IntegrityError:
		return error("Email or institution is already in use or invalid", 409)


class LoginRejected(Exception):
	def __init__(self, reason: str, message: str, status: int):
		super().__init__(message)
		self.reason = reason
		self.message = message
		self.status = status


# --- Sign-in audit (PEF-DS07 access_audit; read through AuditService) ---------------------------

_CORRELATION = re.compile(r"^[A-Za-z0-9._:\-]{1,64}$")
AUDIT_INSERT = text(
	"""
	INSERT INTO access_audit (id, occurred_at, actor_type, actor_user_id, action, resource_type, resource_id,
	                          institution_id, outcome, correlation_id, ip)
	VALUES (CAST(:id AS uuid), now(), 'user', CAST(:user_id AS uuid), :action, 'user', CAST(:user_id AS uuid),
	        CAST(:institution_id AS uuid), :outcome, :correlation_id, CAST(:ip AS inet))
	"""
)


def _audit_values(user_id, institution_id, action: str, outcome: str) -> dict[str, Any]:
	import ipaddress

	incoming = request.headers.get("X-Correlation-ID", "")
	try:   # the connection's address, never a client-supplied X-Forwarded-For
		ip = str(ipaddress.ip_address((request.remote_addr or "").strip()))
	except ValueError:
		ip = None
	return {"id": str(uuid.uuid4()), "user_id": str(user_id), "institution_id": str(institution_id), "action": action,
			"outcome": outcome, "ip": ip,
			"correlation_id": incoming if _CORRELATION.fullmatch(incoming) else uuid.uuid4().hex}


def record_sign_in_event(user_id, institution_id, action: str, outcome: str = "success") -> None:
	"""LOGIN_FAILED / LOGOUT in their own transaction: an audit failure never blocks signing in or out."""
	try:
		with engine.begin() as connection:
			connection.execute(AUDIT_INSERT, _audit_values(user_id, institution_id, action, outcome))
	except SQLAlchemyError:
		app.logger.warning("could not record %s for user %s", action, user_id)


def client_ip_from_request() -> str:
	return (request.headers.get("X-Forwarded-For") or request.remote_addr or "unknown").split(",")[0].strip()


def authenticate_credentials(email: str, password: str, client_ip: str):
	"""Shared credential check for legacy /login and /api/v1/auth/login.

	Rate limit, user + institution active, password hash, fail counters, last_login_at.
	Returns (user row, role rows with code/description/institution_id).
	Raises LoginRejected; RedisError / SQLAlchemyError propagate.
	"""
	fail_identity = email or client_ip
	if token_store.is_login_blocked(fail_identity) or token_store.is_login_blocked(client_ip):
		raise LoginRejected("rate_limited", "Too many failed login attempts. Try again later.", 429)

	with engine.connect() as connection:
		user = connection.execute(
			text(
				"""
				SELECT u.id, u.name, u.email, u.password_hash, u.active, u.ui_preferences,
				       u.institution_id, i.active AS institution_active
				FROM "user" u
				JOIN institution i ON i.id = u.institution_id
				WHERE u.email = :email
				"""
			),
			{"email": email},
		).mappings().first()

	if (
		not user
		or not user["active"]
		or not user["institution_active"]
		or not check_password_hash(user["password_hash"], password)
	):
		token_store.register_login_failure(fail_identity)
		token_store.register_login_failure(client_ip)
		if user:
			# A failed attempt on a known account (wrong password, inactive user or institution).
			record_sign_in_event(user["id"], user["institution_id"], "LOGIN_FAILED", "denied")
		raise LoginRejected("invalid_credentials", "Invalid credentials", 401)

	token_store.clear_login_failures(fail_identity)
	token_store.clear_login_failures(client_ip)

	with engine.begin() as connection:
		connection.execute(text("UPDATE \"user\" SET last_login_at = now() WHERE id = :id"), {"id": user["id"]})
		# Same transaction as last_login_at: a sign-in is recorded exactly once.
		connection.execute(AUDIT_INSERT, _audit_values(user["id"], user["institution_id"], "LOGIN", "success"))
	with engine.connect() as connection:
		role_rows = connection.execute(
			text(
				"""
				SELECT r.code, r.description, r.institution_id
				FROM user_role ur
				JOIN role r ON r.id = ur.role_id
				WHERE ur.user_id = :id
				  AND r.institution_id = :institution_id
				ORDER BY r.code
				"""
			),
			{"id": user["id"], "institution_id": user["institution_id"]},
		).mappings().all()
	return user, role_rows


@app.post("/login")
def login():
	payload = request_json()
	email = str(payload.get("email", "")).strip().lower()
	password = str(payload.get("password", ""))
	try:
		user, role_rows = authenticate_credentials(email, password, client_ip_from_request())
	except LoginRejected as exc:
		return error(exc.message, exc.status)
	except RedisError:
		return error("Session store unavailable", 503)

	institution_id = str(user["institution_id"])
	ui_preferences = normalize_ui_preferences(user["ui_preferences"])
	role_code = _primary_role_code(role_rows)
	try:
		access, refresh_tok, access_age, refresh_age = _issue_pair_for_user(
			str(user["id"]), institution_id, role_code,
		)
	except PermissionError:
		return error("Session expired; sign in again.", 401)
	response = jsonify({
		"token_type": "Bearer",
		"user": {
			"id": str(user["id"]),
			"name": user["name"],
			"email": user["email"],
			"institution_id": institution_id,
			"ui_preferences": ui_preferences,
			"roles": [{"code": role["code"], "description": role["description"]} for role in role_rows],
		},
	})
	return set_auth_cookies(
		response, access, refresh_tok,
		access_max_age=access_age, refresh_max_age=refresh_age,
	)


class RefreshDenied(Exception):
	"""Refresh refused by session policy; the presented refresh token is already revoked."""

	def __init__(self, message: str):
		super().__init__(message)
		self.message = message


def _claim_time(claims: dict[str, Any], name: str, fallback: datetime) -> datetime:
	raw = claims.get(name)
	try:
		return datetime.fromtimestamp(int(raw), tz=timezone.utc) if raw else fallback
	except (TypeError, ValueError, OSError):
		return fallback


def rotate_refresh(
	claims: dict[str, Any],
	user_id: str,
	institution_id: str | None,
	role_code: str,
	*,
	touch_activity: bool,
) -> tuple[str, str, int, int]:
	"""Shared refresh rules (legacy /refresh and /api/v1/auth/refresh).

	Enforces max session wall-clock and idle timeout (OP exempt while a surgery is in
	progress), revokes the presented refresh token and issues a new access/refresh pair.
	"""
	policy = _token_policy(role_code)
	now = utc_now()
	sas = _claim_time(claims, "sas", now)

	def deny(message: str) -> RefreshDenied:
		revoke_token(claims["jti"])
		return RefreshDenied(message)

	# Absolute max auth session.
	if (now - sas).total_seconds() > policy["max_session"]:
		raise deny("Maximum session duration exceeded; sign in again.")

	# SPD / admin idle timeout. OP skips idle while surgery still in_progress
	# (until the session is submitted for SPD review).
	surgery_active = role_code == "station_operator" and _operator_has_active_surgery(user_id)
	if policy["idle"] and not surgery_active:
		if (now - _claim_time(claims, "la", sas)).total_seconds() > policy["idle"]:
			raise deny("Signed out due to inactivity.")

	# Preserve prior last-activity when this is a silent renew.
	prior_activity = _claim_time(claims, "la", sas)

	revoke_token(claims["jti"])
	try:
		return _issue_pair_for_user(
			user_id,
			institution_id,
			role_code,
			auth_session_started=sas,
			last_activity=prior_activity,
			touch_activity=touch_activity,
		)
	except PermissionError:
		raise deny("Maximum session duration exceeded; sign in again.") from None


@app.post("/refresh")
@token_required("refresh")
def refresh():
	claims = g.auth_claims
	role_code = claims.get("role") or "default"
	# Resolve current role from DB (role changes / multi-role).
	with engine.connect() as connection:
		role_rows = connection.execute(
			text(
				"""
				SELECT r.code
				FROM user_role ur
				JOIN role r ON r.id = ur.role_id
				JOIN "user" u ON u.id = ur.user_id AND u.institution_id = r.institution_id
				WHERE ur.user_id = CAST(:id AS uuid)
				ORDER BY r.code
				"""
			),
			{"id": g.auth_user_id},
		).mappings().all()
	if role_rows:
		role_code = _primary_role_code(role_rows)

	# Heartbeat may pass touch=0 so SPD idle is not reset by a silent timer.
	touch_raw = request.args.get("touch", request.headers.get("X-Auth-Touch", "1"))
	touch_activity = str(touch_raw).strip().lower() not in {"0", "false", "no"}

	try:
		access, new_refresh, access_age, refresh_age = rotate_refresh(
			claims, g.auth_user_id, g.auth_institution_id, role_code, touch_activity=touch_activity,
		)
	except RefreshDenied as exc:
		body, status = error(exc.message, 401)
		return clear_auth_cookies(body), status

	return set_auth_cookies(
		jsonify({"token_type": "Bearer"}),
		access,
		new_refresh,
		access_max_age=access_age,
		refresh_max_age=refresh_age,
	)


@app.post("/logout")
@token_required(None)
def logout():
	revoke_token(g.auth_claims["jti"])
	if g.auth_institution_id:
		record_sign_in_event(g.auth_user_id, g.auth_institution_id, "LOGOUT")
	# Also revoke the sibling cookie when both are present.
	for cookie_name in ("access_token", "refresh_token"):
		raw = request.cookies.get(cookie_name)
		if not raw:
			continue
		try:
			claims = jwt.decode(
				raw,
				app.config["JWT_SECRET_KEY"],
				algorithms=[app.config["JWT_ALGORITHM"]],
			)
			if claims.get("jti") and claims["jti"] != g.auth_claims["jti"]:
				revoke_token(claims["jti"])
		except jwt.InvalidTokenError:
			continue
	return clear_auth_cookies(jsonify({"message": "Logged out"}))


@app.get("/verify")
@token_required("access")
def verify():
	with engine.connect() as connection:
		user = connection.execute(
			text(
				"""
				SELECT u.id, u.name, u.email, u.institution_id, u.ui_preferences,
				       r.code AS role_code, r.description AS role_description
				FROM "user" u
				LEFT JOIN user_role ur ON ur.user_id = u.id
				LEFT JOIN role r ON r.id = ur.role_id AND r.institution_id = u.institution_id
				WHERE u.id = :id
				ORDER BY r.code
				"""
			),
			{"id": g.auth_user_id},
		).mappings().all()
	if not user:
		return error("User not found", 401)
	first_user = user[0]
	roles = [
		{"code": row["role_code"], "description": row["role_description"]}
		for row in user
		if row["role_code"]
	]
	return jsonify({
		"valid": True,
		"user": {
			"id": str(first_user["id"]),
			"name": first_user["name"],
			"email": first_user["email"],
			"institution_id": str(first_user["institution_id"]),
			"ui_preferences": normalize_ui_preferences(first_user["ui_preferences"]),
			"roles": roles,
		},
	})


@app.post("/preferences")
@token_required("access")
def update_preferences():
	"""Partial merge into user.ui_preferences (DS01): only the keys sent are replaced."""
	payload = request_json()
	changes: dict[str, Any] = {}
	if "locale" in payload:
		if payload["locale"] not in SUPPORTED_LOCALES:
			return error("locale must be one of: en, es-MX", 400)
		changes["locale"] = payload["locale"]
	if "theme" in payload:
		if payload["theme"] not in SUPPORTED_THEMES:
			return error("theme must be one of: light, dark, system", 400)
		changes["theme"] = payload["theme"]
	if not changes:
		return error("No supported preference provided", 400)

	with engine.begin() as connection:
		row = connection.execute(
			text("SELECT ui_preferences FROM \"user\" WHERE id = :id FOR UPDATE"),
			{"id": g.auth_user_id},
		).first()
		if not row:
			return error("User not found", 404)
		stored = dict(row[0]) if isinstance(row[0], dict) else {}
		if "locale" in changes:
			stored.pop("language", None)  # legacy alias of locale, superseded
		stored.update(changes)
		connection.execute(
			text(
				"UPDATE \"user\" SET ui_preferences = CAST(:prefs AS jsonb), updated_at = now() "
				"WHERE id = :id"
			),
			{"prefs": json.dumps(stored), "id": g.auth_user_id},
		)
	return jsonify({"ui_preferences": normalize_ui_preferences(stored)})


@app.post("/password/forgot")
def password_forgot():
	email = str(request_json().get("email", "")).strip().lower()
	with engine.connect() as connection:
		user = connection.execute(
			text(
				"""
				SELECT u.id, u.institution_id
				FROM "user" u
				WHERE u.email = :email AND u.active = true
				"""
			),
			{"email": email},
		).mappings().first()
	response: dict[str, Any] = {"message": "If the account exists, password reset instructions were created"}
	if user:
		reset_token = issue_token(
			str(user["id"]),
			"password_reset",
			timedelta(minutes=app.config["RESET_TOKEN_MINUTES"]),
			institution_id=str(user["institution_id"]),
		)
		if app.config["RETURN_RESET_TOKEN"]:
			response["reset_token"] = reset_token
	return jsonify(response)


@app.post("/password/reset")
@token_required("password_reset")
def password_reset():
	password = str(request_json().get("password", ""))
	if len(password) < 8:
		return error("Password must have at least 8 characters", 400)
	with engine.begin() as connection:
		connection.execute(text("UPDATE \"user\" SET password_hash = :hash, password_updated_at = now() WHERE id = :id"),
						   {"hash": generate_password_hash(password), "id": g.auth_user_id})
	revoke_token(g.auth_claims["jti"])
	return jsonify({"message": "Password reset successfully"})


@app.post("/password/change")
@token_required("access")
def password_change():
	payload = request_json()
	current = str(payload.get("current_password", ""))
	new_password = str(payload.get("new_password", ""))
	if len(new_password) < 8:
		return error("new_password must have at least 8 characters", 400)
	with engine.connect() as connection:
		user = connection.execute(text("SELECT password_hash FROM \"user\" WHERE id = :id"), {"id": g.auth_user_id}).first()
	if not user or not check_password_hash(user[0], current):
		return error("Invalid current password", 401)
	with engine.begin() as connection:
		connection.execute(text("UPDATE \"user\" SET password_hash = :hash, password_updated_at = now() WHERE id = :id"),
						   {"hash": generate_password_hash(new_password), "id": g.auth_user_id})
	return jsonify({"message": "Password changed successfully"})


# Versioned API for Android/Desktop (/api/v1/auth/*) + DS09 health checks, built on the
# shared rules above. Registered last so it can reference every helper of this module.
register_api(app, sys.modules[__name__])


if __name__ == "__main__":
	app.run(host=os.getenv("HOST", "0.0.0.0"), port=int(os.getenv("PORT", "5001")), debug=app.debug)
