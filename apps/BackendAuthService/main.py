from __future__ import annotations

import os
import threading
import uuid
from datetime import datetime, timedelta, timezone
from functools import wraps
from typing import Any, Callable

import jwt
from dotenv import load_dotenv
from flask import Flask, g, jsonify, request
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.engine import Engine
from werkzeug.security import check_password_hash, generate_password_hash


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
	ACCESS_TOKEN_MINUTES=seconds_from_env("ACCESS_TOKEN_MINUTES", 15),
	REFRESH_TOKEN_DAYS=seconds_from_env("REFRESH_TOKEN_DAYS", 30),
	RESET_TOKEN_MINUTES=seconds_from_env("RESET_TOKEN_MINUTES", 30),
	RETURN_RESET_TOKEN=os.getenv("AUTH_RETURN_RESET_TOKEN", "true").lower() == "true",
	COOKIE_DOMAIN=os.getenv("AUTH_COOKIE_DOMAIN") or None,
	COOKIE_SECURE=os.getenv("AUTH_COOKIE_SECURE", "true").lower() == "true",
	COOKIE_SAMESITE=os.getenv("AUTH_COOKIE_SAMESITE", "None"),
)

if app.config["JWT_SECRET_KEY"] == "change-this-secret" and not app.debug:
	raise RuntimeError("JWT_SECRET_KEY must be configured outside debug mode")

engine: Engine = create_engine(build_database_url(), pool_pre_ping=True, future=True)
token_store: dict[str, dict[str, Any]] = {}
token_store_lock = threading.Lock()


def token_from_request(token_type: str | None) -> str | None:
	header = request.headers.get("Authorization", "")
	if header.startswith("Bearer "):
		return header[7:]
	if token_type == "refresh":
		return request.cookies.get("refresh_token")
	return request.cookies.get("access_token")


def set_auth_cookies(response, access_token: str, refresh_token: str):
	common = {
		"domain": app.config["COOKIE_DOMAIN"],
		"secure": app.config["COOKIE_SECURE"],
		"httponly": True,
		"samesite": app.config["COOKIE_SAMESITE"],
		"path": "/",
	}
	response.set_cookie("access_token", access_token, **common)
	response.set_cookie("refresh_token", refresh_token, **common)
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


def issue_token(user_id: str, token_type: str, lifetime: timedelta) -> str:
	issued_at = utc_now()
	token_id = str(uuid.uuid4())
	expires_at = issued_at + lifetime
	token = jwt.encode(
		{
			"sub": user_id,
			"jti": token_id,
			"type": token_type,
			"iat": issued_at,
			"exp": expires_at,
		},
		app.config["JWT_SECRET_KEY"],
		algorithm=app.config["JWT_ALGORITHM"],
	)
	with token_store_lock:
		for stored_jti, stored_token in list(token_store.items()):
			if stored_token["expires_at"] <= issued_at:
				del token_store[stored_jti]
		token_store[token_id] = {
			"user_id": user_id,
			"token_type": token_type,
			"expires_at": expires_at,
			"revoked": False,
		}
	return token


def revoke_token(jti: str) -> None:
	with token_store_lock:
		stored_token = token_store.get(jti)
		if stored_token:
			stored_token["revoked"] = True


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
				with token_store_lock:
					token = token_store.get(claims["jti"])
				if not token or token["revoked"] or token["expires_at"] <= utc_now():
					return error("Token is revoked or user is inactive", 401)
				if token["token_type"] != claims.get("type"):
					return error("Invalid token type", 401)
				with engine.connect() as connection:
					user = connection.execute(
						text("SELECT active FROM \"user\" WHERE id = :id"),
						{"id": token["user_id"]},
					).first()
				if not user or not user[0]:
					return error("Token is revoked or user is inactive", 401)
				g.auth_claims = claims
				g.auth_user_id = token["user_id"]
			except (jwt.InvalidTokenError, KeyError, SQLAlchemyError):
				return error("Invalid or expired token", 401)
			return function(*args, **kwargs)

		return wrapped

	return decorator


@app.errorhandler(SQLAlchemyError)
def database_error(exception):
	app.logger.exception("Database error", exc_info=exception)
	return error("Database unavailable", 503)


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
	with engine.connect() as connection:
		user = connection.execute(
			text("SELECT id, name, email, password_hash, active FROM \"user\" WHERE email = :email"),
			{"email": email},
		).mappings().first()
	if not user or not user["active"] or not check_password_hash(user["password_hash"], password):
		return error("Invalid credentials", 401)
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
	access = issue_token(str(user["id"]), "access", timedelta(minutes=app.config["ACCESS_TOKEN_MINUTES"]))
	refresh = issue_token(str(user["id"]), "refresh", timedelta(days=app.config["REFRESH_TOKEN_DAYS"]))
	response = jsonify({"token_type": "Bearer",
					"user": {
						"id": str(user["id"]),
						"name": user["name"],
						"email": user["email"],
						"roles": [dict(role) for role in role_rows],
					}})
	return set_auth_cookies(response, access, refresh)


@app.post("/refresh")
@token_required("refresh")
def refresh():
	revoke_token(g.auth_claims["jti"])
	access = issue_token(g.auth_user_id, "access", timedelta(minutes=app.config["ACCESS_TOKEN_MINUTES"]))
	new_refresh = issue_token(g.auth_user_id, "refresh", timedelta(days=app.config["REFRESH_TOKEN_DAYS"]))
	return set_auth_cookies(jsonify({"token_type": "Bearer"}), access, new_refresh)


@app.post("/logout")
@token_required(None)
def logout():
	revoke_token(g.auth_claims["jti"])
	return clear_auth_cookies(jsonify({"message": "Logged out"}))


@app.get("/verify")
@token_required("access")
def verify():
	with engine.connect() as connection:
		user = connection.execute(
			text(
				"""
				SELECT u.id, u.name, u.email, u.institution_id,
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
			"roles": roles,
		},
	})


@app.post("/password/forgot")
def password_forgot():
	email = str(request_json().get("email", "")).strip().lower()
	with engine.connect() as connection:
		user = connection.execute(text("SELECT id FROM \"user\" WHERE email = :email AND active = true"), {"email": email}).first()
	response: dict[str, Any] = {"message": "If the account exists, password reset instructions were created"}
	if user:
		reset_token = issue_token(str(user[0]), "password_reset", timedelta(minutes=app.config["RESET_TOKEN_MINUTES"]))
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
