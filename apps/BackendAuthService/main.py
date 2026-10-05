from __future__ import annotations

import os
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
	COOKIE_DOMAIN=os.getenv("AUTH_COOKIE_DOMAIN") or None,
	COOKIE_SECURE=os.getenv("AUTH_COOKIE_SECURE", "true").lower() == "true",
	COOKIE_SAMESITE=os.getenv("AUTH_COOKIE_SAMESITE", "None"),
)

if app.config["JWT_SECRET_KEY"] == "change-this-secret" and not app.debug:
	raise RuntimeError("JWT_SECRET_KEY must be configured outside debug mode")

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


def normalize_ui_preferences(raw: Any) -> dict[str, Any]:
	prefs = dict(raw) if isinstance(raw, dict) else {}
	locale = prefs.get("locale") or prefs.get("language") or "en"
	if locale == "es":
		locale = "es-MX"
	if locale not in {"en", "es-MX"}:
		locale = "en"
	theme = prefs.get("theme") or "light"
	if theme not in {"light", "dark", "system"}:
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


def token_required(token_type: str | None = "access"):
	def decorator(function: Callable):
		@wraps(function)
		def wrapped(*args, **kwargs):
			raw_token = token_from_request(token_type)
			if not raw_token:
				return error("Bearer token is required", 401)
			try:
				claims = jwt.decode(
					raw_token,
					app.config["JWT_SECRET_KEY"],
					algorithms=[app.config["JWT_ALGORITHM"]],
				)
				if token_type and claims.get("type") != token_type:
					return error("Invalid token type", 401)
				stored = token_store.get_token(claims["jti"])
				if not stored or stored.get("revoked"):
					return error("Token is revoked or user is inactive", 401)
				if stored.get("token_type") != claims.get("type"):
					return error("Invalid token type", 401)
				with engine.connect() as connection:
					user = connection.execute(
						text("SELECT active FROM \"user\" WHERE id = :id"),
						{"id": stored["user_id"]},
					).first()
				if not user or not user[0]:
					return error("Token is revoked or user is inactive", 401)
				g.auth_claims = claims
				g.auth_user_id = stored["user_id"]
				g.auth_institution_id = stored.get("institution_id")
			except RedisError:
				return error("Session store unavailable", 503)
			except (jwt.InvalidTokenError, KeyError, SQLAlchemyError):
				return error("Invalid or expired token", 401)
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


@app.get("/health/redis")
def health_redis():
	try:
		ok = token_store.ping()
	except RedisError:
		ok = False
	return jsonify({"ok": ok}), 200 if ok else 503


@app.route("/login", methods=["OPTIONS"])
@app.route("/logout", methods=["OPTIONS"])
@app.route("/refresh", methods=["OPTIONS"])
@app.route("/verify", methods=["OPTIONS"])
def cors_preflight():
	return ("", 204)


@app.post("/register")
def register():
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


@app.post("/login")
def login():
	payload = request_json()
	email = str(payload.get("email", "")).strip().lower()
	password = str(payload.get("password", ""))
	client_ip = (request.headers.get("X-Forwarded-For") or request.remote_addr or "unknown").split(",")[0].strip()
	fail_identity = email or client_ip

	try:
		if token_store.is_login_blocked(fail_identity) or token_store.is_login_blocked(client_ip):
			return error("Too many failed login attempts. Try again later.", 429)
	except RedisError:
		return error("Session store unavailable", 503)

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
		try:
			token_store.register_login_failure(fail_identity)
			token_store.register_login_failure(client_ip)
		except RedisError:
			return error("Session store unavailable", 503)
		return error("Invalid credentials", 401)

	try:
		token_store.clear_login_failures(fail_identity)
		token_store.clear_login_failures(client_ip)
	except RedisError:
		return error("Session store unavailable", 503)

	with engine.begin() as connection:
		connection.execute(text("UPDATE \"user\" SET last_login_at = now() WHERE id = :id"), {"id": user["id"]})
	with engine.connect() as connection:
		role_rows = connection.execute(
			text(
				"""
				SELECT r.code, r.description
				FROM user_role ur
				JOIN role r ON r.id = ur.role_id
				WHERE ur.user_id = :id
				ORDER BY r.code
				"""
			),
			{"id": user["id"]},
		).mappings().all()

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
			"roles": [dict(role) for role in role_rows],
		},
	})
	return set_auth_cookies(
		response, access, refresh_tok,
		access_max_age=access_age, refresh_max_age=refresh_age,
	)


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
				WHERE ur.user_id = CAST(:id AS uuid)
				ORDER BY r.code
				"""
			),
			{"id": g.auth_user_id},
		).mappings().all()
	if role_rows:
		role_code = _primary_role_code(role_rows)

	policy = _token_policy(role_code)
	now = utc_now()
	sas_ts = claims.get("sas")
	try:
		sas = datetime.fromtimestamp(int(sas_ts), tz=timezone.utc) if sas_ts else now
	except (TypeError, ValueError, OSError):
		sas = now

	def _deny_refresh(message: str):
		revoke_token(claims["jti"])
		body, status = error(message, 401)
		return clear_auth_cookies(body), status

	# Absolute max auth session.
	if (now - sas).total_seconds() > policy["max_session"]:
		return _deny_refresh("Maximum session duration exceeded; sign in again.")

	# SPD / admin idle timeout. OP skips idle while surgery still in_progress
	# (until the session is submitted for SPD review).
	surgery_active = role_code == "station_operator" and _operator_has_active_surgery(g.auth_user_id)
	if policy["idle"] and not surgery_active:
		la_ts = claims.get("la")
		try:
			last_activity = datetime.fromtimestamp(int(la_ts), tz=timezone.utc) if la_ts else sas
		except (TypeError, ValueError, OSError):
			last_activity = sas
		if (now - last_activity).total_seconds() > policy["idle"]:
			return _deny_refresh("Signed out due to inactivity.")

	# Heartbeat may pass touch=0 so SPD idle is not reset by a silent timer.
	touch_raw = request.args.get("touch", request.headers.get("X-Auth-Touch", "1"))
	touch_activity = str(touch_raw).strip().lower() not in {"0", "false", "no"}

	# Preserve prior last-activity when this is a silent renew.
	la_ts = claims.get("la")
	try:
		prior_activity = datetime.fromtimestamp(int(la_ts), tz=timezone.utc) if la_ts else sas
	except (TypeError, ValueError, OSError):
		prior_activity = sas

	revoke_token(claims["jti"])
	try:
		access, new_refresh, access_age, refresh_age = _issue_pair_for_user(
			g.auth_user_id,
			g.auth_institution_id,
			role_code,
			auth_session_started=sas,
			last_activity=prior_activity,
			touch_activity=touch_activity,
		)
	except PermissionError:
		return _deny_refresh("Maximum session duration exceeded; sign in again.")

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
				LEFT JOIN role r ON r.id = ur.role_id
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


if __name__ == "__main__":
	app.run(host=os.getenv("HOST", "0.0.0.0"), port=int(os.getenv("PORT", "5001")), debug=app.debug)
