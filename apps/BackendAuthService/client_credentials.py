"""MS-07: machine clients (PEF-DS08) — OAuth 2.0 client credentials on AuthService.

A hospital system or another integration authenticates as an `integration_client` row (client_id + secret,
hashed with the same werkzeug scheme as user passwords, never stored in clear), never as a `user`:

    POST /api/v1/auth/token
      grant_type=client_credentials, client_id, client_secret[, scope]
      (application/x-www-form-urlencoded as in RFC 6749 §4.4, or JSON; HTTP Basic is also accepted)
    -> {"access_token", "token_type": "Bearer", "expires_in", "scope"}

The access token is a JWT with `type = client_access`, `sub` = integration_client.id, the client's institution
and its granted scopes. It is registered in Redis (`auth:jwt:{jti}`, token_type client_access) and indexed per
client (`auth:client_tokens:{id}`) so it can be revoked. There is no refresh token: the client authenticates
again. Every PEF microservice that serves users checks `type == access` and a `user` row, so a client token is
refused there (it cannot pass for a user nor for an internal service); only the services that explicitly
accept machine clients (IntegrationService) validate `client_access` tokens, the client row (active, same
institution) and the scopes stored in PostgreSQL (never only the claim).

    POST /api/v1/auth/clients/{id}/revoke   (Bearer of an it_admin of the client's institution)
      -> revokes every live token of the client (deactivation and secret rotation call it).

Failures are throttled like logins (`auth:login_fail:client:<client_id>` and the caller IP) and audited in
access_audit with actor_type = client when the client exists (no secret, no token is ever logged).
"""

from __future__ import annotations

import base64
import json
import logging
import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import jwt
from flask import Blueprint, g, request
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from werkzeug.security import check_password_hash

from api_format import ApiError, render

logger = logging.getLogger("auth.clients")

CLIENT_TOKEN_TYPE = "client_access"
CLIENT_ID_PATTERN = re.compile(r"^[A-Za-z0-9._:\-]{3,128}$")
SCOPE_PATTERN = re.compile(r"^[a-z][a-z0-9_.]{1,63}$")
MAX_BODY_BYTES = 8 * 1024
ADMIN = "it_admin"


def _utc_now() -> datetime:
	return datetime.now(timezone.utc)


def _client_tokens_key(client_uuid: str) -> str:
	return f"auth:client_tokens:{client_uuid}"


def _form() -> dict[str, str]:
	"""Form (RFC 6749) or JSON body; HTTP Basic credentials override the body ones."""
	if request.content_length is not None and request.content_length > MAX_BODY_BYTES:
		raise ApiError(413)
	if request.mimetype == "application/x-www-form-urlencoded":
		values = {key: request.form.get(key) for key in request.form}
	elif request.mimetype == "application/json":
		try:
			payload = json.loads(request.get_data(cache=True).decode("utf-8") or "{}")
		except (UnicodeDecodeError, ValueError):
			raise ApiError(400, "Request body is not valid JSON", error="invalid_request") from None
		if not isinstance(payload, dict) or any(v is not None and not isinstance(v, str) for v in payload.values()):
			raise ApiError(400, "Request body must be an object of strings", error="invalid_request")
		values = payload
	else:
		raise ApiError(400, "Content-Type must be application/x-www-form-urlencoded or application/json",
					   error="invalid_request")
	unknown = sorted(set(values) - {"grant_type", "client_id", "client_secret", "scope"})
	if unknown:
		raise ApiError(400, "Unknown fields in request body", error="invalid_request", details={"unknown_fields": unknown})
	scheme, _, encoded = request.headers.get("Authorization", "").partition(" ")
	if scheme.lower() == "basic" and encoded:
		try:
			decoded = base64.b64decode(encoded.strip(), validate=True).decode("utf-8")
			basic_id, _, basic_secret = decoded.partition(":")
		except (ValueError, UnicodeDecodeError):
			raise ApiError(401, "Invalid client authentication", error="invalid_client") from None
		values["client_id"], values["client_secret"] = basic_id, basic_secret
	return values


def register_client_credentials(api_bp: Blueprint, core, require_bearer) -> None:

	def audit(connection, action: str, client_row_id, institution_id, outcome: str = "success") -> None:
		import ipaddress
		try:
			ip = str(ipaddress.ip_address(core.client_ip_from_request().strip()))
		except ValueError:
			ip = None
		incoming = request.headers.get("X-Correlation-ID", "")
		connection.execute(text("""
			INSERT INTO access_audit (id, occurred_at, actor_type, actor_client_id, action, resource_type, resource_id,
			                          institution_id, outcome, correlation_id, ip)
			VALUES (CAST(:id AS uuid), now(), 'client', CAST(:client AS uuid), :action, 'integration_client',
			        CAST(:client AS uuid), CAST(:inst AS uuid), :outcome, :corr, CAST(:ip AS inet))
		"""), {"id": str(uuid.uuid4()), "client": str(client_row_id), "action": action, "inst": str(institution_id),
			   "outcome": outcome, "ip": ip,
			   "corr": incoming if re.fullmatch(r"[A-Za-z0-9._:\-]{1,64}", incoming or "") else uuid.uuid4().hex})

	def audit_safely(action: str, client_row_id, institution_id, outcome: str = "success") -> None:
		try:
			with core.engine.begin() as connection:
				audit(connection, action, client_row_id, institution_id, outcome)
		except SQLAlchemyError:
			logger.warning("could not audit %s for client %s", action, client_row_id)

	def store(jti: str, client_row: dict[str, Any], expires_at: datetime) -> None:
		ttl = max(1, int((expires_at - _utc_now()).total_seconds()))
		payload = {"jti": jti, "client_id": str(client_row["id"]), "user_id": None,
				   "institution_id": str(client_row["institution_id"]), "token_type": CLIENT_TOKEN_TYPE, "revoked": False,
				   "exp": expires_at.isoformat().replace("+00:00", "Z")}
		redis = core.token_store.client
		index = _client_tokens_key(str(client_row["id"]))
		pipeline = redis.pipeline()
		pipeline.set(f"auth:jwt:{jti}", json.dumps(payload), ex=ttl)
		pipeline.sadd(index, jti)
		pipeline.expire(index, max(ttl, redis.ttl(index) or 0))
		pipeline.execute()

	@api_bp.post("/token")
	def client_token():
		values = _form()
		if (values.get("grant_type") or "") != "client_credentials":
			raise ApiError(400, "Only grant_type=client_credentials is supported here", error="unsupported_grant_type")
		client_id, secret = (values.get("client_id") or "").strip(), values.get("client_secret") or ""
		if not CLIENT_ID_PATTERN.fullmatch(client_id) or not secret:
			raise ApiError(401, "Invalid client authentication", error="invalid_client")
		ip = core.client_ip_from_request()
		fail_identity = f"client:{client_id}"
		if core.token_store.is_login_blocked(fail_identity) or core.token_store.is_login_blocked(ip):
			raise ApiError(429, "Too many failed attempts. Try again later.", error="rate_limited")
		with core.engine.connect() as connection:
			row = connection.execute(text("""
				SELECT c.id, c.client_id, c.secret_hash, c.scopes, c.active, c.institution_id, i.active AS institution_active
				FROM integration_client c JOIN institution i ON i.id = c.institution_id WHERE c.client_id = :cid
			"""), {"cid": client_id}).mappings().first()
		valid = bool(row) and check_password_hash(row["secret_hash"], secret)
		if not valid or not row["active"] or not row["institution_active"]:
			core.token_store.register_login_failure(fail_identity)
			core.token_store.register_login_failure(ip)
			if row:
				audit_safely("CLIENT_TOKEN_DENIED", row["id"], row["institution_id"], outcome="denied")
			# Same answer for an unknown client, a wrong secret or a deactivated client (no enumeration).
			raise ApiError(401, "Invalid client authentication", error="invalid_client")
		granted = [s for s in (row["scopes"] or []) if isinstance(s, str)]
		requested = (values.get("scope") or "").split()
		if any(not SCOPE_PATTERN.fullmatch(s) for s in requested):
			raise ApiError(400, "Malformed scope", error="invalid_scope")
		if requested and not set(requested) <= set(granted):
			raise ApiError(400, "Requested scope was not granted to this client", error="invalid_scope",
						   details={"not_granted": sorted(set(requested) - set(granted))})
		scopes = sorted(set(requested or granted))
		core.token_store.clear_login_failures(fail_identity)
		lifetime = int(core.app.config.get("CLIENT_TOKEN_SECONDS") or 900)
		now = _utc_now()
		jti, expires_at = str(uuid.uuid4()), now + timedelta(seconds=lifetime)
		token = jwt.encode({"sub": str(row["id"]), "jti": jti, "type": CLIENT_TOKEN_TYPE, "iat": now, "exp": expires_at,
							"cid": row["client_id"], "inst": str(row["institution_id"]), "scope": " ".join(scopes)},
						   core.app.config["JWT_SECRET_KEY"], algorithm=core.app.config["JWT_ALGORITHM"])
		store(jti, row, expires_at)
		try:
			with core.engine.begin() as connection:
				connection.execute(text("UPDATE integration_client SET last_used_at = now() WHERE id = :id"), {"id": row["id"]})
				audit(connection, "CLIENT_TOKEN_ISSUED", row["id"], row["institution_id"])
		except SQLAlchemyError:
			logger.warning("could not record the token issue of client %s", row["id"])
		logger.info("client token issued client=%s scopes=%s", row["client_id"], scopes)
		response = render({"access_token": token, "token_type": "Bearer", "expires_in": lifetime,
						   "scope": " ".join(scopes)}, root="client_token_response")
		response.headers["Cache-Control"] = "no-store"
		response.headers["Pragma"] = "no-cache"
		return response

	@api_bp.post("/clients/<client_uuid>/revoke")
	@require_bearer
	def revoke_client(client_uuid: str):
		identity = g.api_identity
		if ADMIN not in (identity.get("roles") or []):
			raise ApiError(403)
		try:
			target = uuid.UUID(client_uuid)
		except ValueError:
			raise ApiError(404) from None
		with core.engine.connect() as connection:
			row = connection.execute(text("SELECT id, institution_id FROM integration_client WHERE id = :id"),
									 {"id": target}).mappings().first()
		if not row or str(row["institution_id"]) != str(identity["institution_id"]):
			raise ApiError(404)       # another institution's client does not exist for this administrator
		redis = core.token_store.client
		index = _client_tokens_key(str(target))
		revoked = 0
		for jti in redis.smembers(index):
			core.token_store.revoke_token(jti)
			revoked += 1
		redis.delete(index)
		logger.info("client tokens revoked client=%s count=%s by=%s", target, revoked, identity["id"])
		return render({"client_id": str(target), "revoked": revoked}, root="client_revoke_result")
