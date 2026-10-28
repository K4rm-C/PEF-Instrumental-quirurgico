"""Identity administration of PEF (PEF-DS09 §1.3.2 complement v0.1.4), base /api/v1/auth.

Users, roles, role assignment and passwords: the rules the portal's Administration used to run
in-process (apps/BackendWebFlask/services/admin_local/users.py and roles.py), now owned here.

- Access: Bearer access token (same checks as /me) and the effective `it_admin` role of the
  caller's institution; any other role gets 403 and a DENIED_<METHOD>_<RESOURCE> access_audit row.
  Changing one's own password only needs a valid token.
- Scope: as in the monolith, the administrator manages the users and roles of every institution
  (the user form chooses the institution); a role is created in the administrator's institution.
- Every write is one transaction with its access_audit rows (same action names as before:
  CREATE_USER, UPDATE_USER, ACTIVATE_USER, DEACTIVATE_USER, CHANGE_USER_ROLES, RESET_USER_PASSWORD,
  CREATE_ROLE, UPDATE_ROLE, DELETE_ROLE, plus CHANGE_OWN_PASSWORD).
- Errors: 400 malformed body, 404 unknown id, 409 conflicts, 422 invalid values; all with
  details.reason (the portal's message codes). Never a hash, token or secret in a response or log.
- Sessions: deactivating a user, moving them to another institution or resetting their password
  revokes every Redis session of that user; verify_token also checks PostgreSQL on every request,
  so the account is closed even for a token missing from the Redis index. Role changes need no
  revocation: roles are read from PostgreSQL on every request.
- Last administrator: no operation can leave an institution without an active it_admin (checked
  under a transaction-level advisory lock, so two concurrent changes cannot both pass).
"""

from __future__ import annotations

import ipaddress
import logging
import re
import uuid
from datetime import timezone
from functools import wraps
from types import ModuleType
from typing import Any, Callable

from flask import Blueprint, g, request
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from werkzeug.security import check_password_hash, generate_password_hash

from api_format import ApiError, parse_object, render

logger = logging.getLogger("auth.identity")

ADMIN = "it_admin"
SUPERVISOR = "spd_supervisor"
OPERATOR = "station_operator"
SYSTEM_ROLES = frozenset({ADMIN, SUPERVISOR, OPERATOR})
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
ROLE_CODE_RE = re.compile(r"^[a-z][a-z0-9_]*$")
MIN_PASSWORD = 8
NIL_UUID = "00000000-0000-0000-0000-000000000000"
ADMIN_LOCK_KEY = "pef:identity:administrators"

USER_FIELDS = {"name": "string", "email": "string", "institution_id": "string", "role_codes": "string_list",
               "active": "boolean"}
USER_CREATE_FIELDS = {**USER_FIELDS, "password": "string", "confirm_password": "string"}
ROLES_FIELDS = {"role_codes": "string_list"}
RESET_FIELDS = {"new_password": "string", "confirm_new_password": "string"}
CHANGE_FIELDS = {"current_password": "string", "new_password": "string", "confirm_new_password": "string"}
ROLE_CREATE_FIELDS = {"code": "string", "description": "string"}
ROLE_UPDATE_FIELDS = {"description": "string"}


def _invalid(reason: str, message: str, field: str | None = None, status: int = 422) -> ApiError:
	details: dict[str, Any] = {"reason": reason}
	if field:
		details["field"] = field
	return ApiError(status, message, details=details)


def _conflict(reason: str, message: str, field: str | None = None) -> ApiError:
	return _invalid(reason, message, field, status=409)


def _not_found(reason: str) -> ApiError:
	return ApiError(404, "Resource not found", details={"reason": reason})


def _uuid(value: Any) -> str | None:
	try:
		return str(uuid.UUID(str(value)))
	except (TypeError, ValueError):
		return None


def _iso(value) -> str | None:
	return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z") if value is not None else None


def _text(payload: dict[str, Any], name: str, *, required: bool, max_length: int | None, label: str,
		  reason: str | None = None) -> str:
	"""Trimmed string; same codes as admin_local.common.text (<name>_required, <name>_too_long)."""
	value = (payload.get(name) or "").strip()
	if required and not value:
		raise _invalid(reason or f"{name}_required", f"{label} is required", name)
	if max_length and len(value) > max_length:
		raise _invalid(f"{name}_too_long", f"{label} must contain at most {max_length} characters", name)
	return value


def _password(payload: dict[str, Any], field: str, confirm_field: str) -> str:
	password = payload.get(field) or ""
	if len(password) < MIN_PASSWORD:
		raise _invalid("password_too_short", f"Password must contain at least {MIN_PASSWORD} characters", field)
	if confirm_field in payload and (payload.get(confirm_field) or "") != password:
		raise _invalid("password_mismatch", "Passwords do not match", confirm_field)
	return password


def register_identity_admin(api_bp: Blueprint, core: ModuleType, require_bearer: Callable) -> None:
	"""Adds the identity administration endpoints to the /api/v1/auth blueprint."""

	# --- Access control and audit ----------------------------------------------------------

	def actor() -> dict[str, Any]:
		return g.api_identity

	def audit(connection, action: str, resource_type: str, resource_id, *, outcome: str = "success",
			  principal: dict[str, Any] | None = None) -> None:
		principal = principal or actor()
		connection.execute(
			text(
				"""
				INSERT INTO access_audit (id, occurred_at, actor_type, actor_user_id, action, resource_type,
				                          resource_id, institution_id, outcome, correlation_id, ip)
				VALUES (CAST(:id AS uuid), now(), 'user', CAST(:actor AS uuid), :action, :resource_type,
				        CAST(:resource_id AS uuid), CAST(:institution AS uuid), :outcome, :correlation_id,
				        CAST(:ip AS inet))
				"""
			),
			{
				"id": str(uuid.uuid4()), "actor": str(principal["id"]), "action": action[:64],
				"resource_type": resource_type[:64], "resource_id": str(resource_id or NIL_UUID),
				"institution": str(principal["institution_id"]), "outcome": outcome,
				"correlation_id": (getattr(g, "correlation_id", None) or "")[:64] or None,
				"ip": _client_ip(request.remote_addr),
			},
		)

	def require_admin(resource_type: str):
		"""require_bearer + effective it_admin; a denied attempt is audited in its own transaction."""

		def decorator(view: Callable) -> Callable:
			@require_bearer
			@wraps(view)
			def wrapped(*args, **kwargs):
				if ADMIN not in actor()["roles"]:
					target = next((value for value in (_uuid(v) for v in kwargs.values()) if value), None)
					try:
						with core.engine.begin() as connection:
							audit(connection, f"DENIED_{request.method}_{resource_type}".upper(), resource_type, target,
								  outcome="denied")
					except SQLAlchemyError:
						logger.warning("could not audit denied %s %s", request.method, request.path)
					raise ApiError(403)
				return view(*args, **kwargs)

			return wrapped

		return decorator

	def revoke_sessions(user_id: str, keep: frozenset[str] = frozenset()) -> int:
		try:
			return core.token_store.revoke_user_tokens(str(user_id), keep=keep)
		except Exception:  # noqa: BLE001 - PostgreSQL already closes the account on every request
			logger.exception("could not revoke the Redis sessions of user=%s", user_id)
			return 0

	# --- Reads -------------------------------------------------------------------------------

	USER_SELECT = """
		SELECT u.id, u.name, u.email, u.active, u.institution_id, i.name AS institution_name,
		       u.last_login_at, u.password_updated_at, u.created_at, u.updated_at
		FROM "user" u
		JOIN institution i ON i.id = u.institution_id
	"""

	def roles_by_user(connection, user_id: str | None = None) -> dict[str, list[dict[str, Any]]]:
		rows = connection.execute(
			text(
				"SELECT ur.user_id, r.code, r.description FROM user_role ur JOIN role r ON r.id = ur.role_id "
				+ ("WHERE ur.user_id = CAST(:id AS uuid) " if user_id else "")
				+ "ORDER BY r.code, r.description"
			),
			{"id": user_id} if user_id else {},
		).mappings().all()
		result: dict[str, list[dict[str, Any]]] = {}
		for row in rows:
			result.setdefault(str(row["user_id"]), []).append({"code": row["code"], "description": row["description"]})
		return result

	def user_document(row, roles: list[dict[str, Any]]) -> dict[str, Any]:
		return {
			"id": str(row["id"]),
			"name": row["name"],
			"email": row["email"],
			"active": bool(row["active"]),
			"institution": {"id": str(row["institution_id"]), "name": row["institution_name"]},
			"roles": roles,
			"last_login_at": _iso(row["last_login_at"]),
			"password_updated_at": _iso(row["password_updated_at"]),
			"created_at": _iso(row["created_at"]),
			"updated_at": _iso(row["updated_at"]),
		}

	def load_user(connection, user_id: str, *, for_update: bool = False):
		row = connection.execute(
			text(USER_SELECT + " WHERE u.id = CAST(:id AS uuid)" + (" FOR UPDATE OF u" if for_update else "")),
			{"id": user_id},
		).mappings().first()
		if row is None:
			raise _not_found("user_not_found")
		return row

	def user_detail(connection, user_id: str) -> dict[str, Any]:
		return user_document(load_user(connection, user_id), roles_by_user(connection, user_id).get(user_id, []))

	def existing_user_id(raw: str) -> str:
		user_id = _uuid(raw)
		if user_id is None:
			raise _not_found("user_not_found")
		return user_id

	@api_bp.get("/users")
	@require_admin("user")
	def list_users():
		with core.engine.connect() as connection:
			rows = connection.execute(text(USER_SELECT + " ORDER BY u.name, u.email")).mappings().all()
			roles = roles_by_user(connection)
		return render([user_document(row, roles.get(str(row["id"]), [])) for row in rows], root="managed_users")

	@api_bp.get("/users/<user_id>")
	@require_admin("user")
	def get_user(user_id: str):
		with core.engine.connect() as connection:
			return render(user_detail(connection, existing_user_id(user_id)), root="managed_user")

	# --- User validation (same order and codes as admin_local.users) ------------------------

	def identity_values(connection, payload: dict[str, Any], *, current_institution: str | None = None):
		name = _text(payload, "name", required=True, max_length=160, label="Name")
		email = _text(payload, "email", required=True, max_length=255, label="Email").lower()
		if not EMAIL_RE.match(email):
			raise _invalid("invalid_email", "Enter a valid email address", "email")
		raw_institution = _text(payload, "institution_id", required=True, max_length=None, label="Institution",
								reason="institution_required")
		institution_id = _uuid(raw_institution)
		institution = connection.execute(
			text("SELECT id, active FROM institution WHERE id = CAST(:id AS uuid)"), {"id": institution_id},
		).mappings().first() if institution_id else None
		if institution is None:
			raise _invalid("invalid_institution", "Choose a valid institution", "institution_id")
		if not institution["active"] and institution_id != current_institution:
			raise _invalid("institution_inactive", "Users cannot be added to an inactive institution",
						   "institution_id")
		codes = list(dict.fromkeys(code.strip() for code in (payload.get("role_codes") or []) if code and code.strip()))
		if not codes:
			raise _invalid("roles_required", "Assign at least one role", "role_codes")
		roles = role_ids(connection, institution_id, codes)
		return name, email, institution_id, roles

	def role_ids(connection, institution_id: str, codes: list[str]) -> dict[str, str]:
		rows = connection.execute(
			text("SELECT id, code FROM role WHERE institution_id = CAST(:inst AS uuid) AND code = ANY(:codes)"),
			{"inst": institution_id, "codes": codes},
		).mappings().all()
		found = {row["code"]: str(row["id"]) for row in rows}
		if set(found) != set(codes):
			raise _invalid("invalid_roles", "Choose roles that belong to the user's institution", "role_codes")
		return found

	def email_taken(connection, email: str, except_id: str | None = None) -> bool:
		return connection.execute(
			text('SELECT 1 FROM "user" WHERE lower(email) = :email AND (CAST(:id AS uuid) IS NULL OR id <> CAST(:id AS uuid))'),
			{"email": email, "id": except_id},
		).first() is not None

	def is_effective_admin(connection, user_id: str) -> bool:
		return connection.execute(
			text(
				"""
				SELECT 1 FROM "user" u
				JOIN user_role ur ON ur.user_id = u.id
				JOIN role r ON r.id = ur.role_id AND r.institution_id = u.institution_id
				WHERE u.id = CAST(:id AS uuid) AND u.active AND r.code = :admin
				"""
			),
			{"id": user_id, "admin": ADMIN},
		).first() is not None

	def other_active_admins(connection, institution_id: str, user_id: str) -> int:
		return connection.execute(
			text(
				"""
				SELECT count(DISTINCT u.id) FROM "user" u
				JOIN user_role ur ON ur.user_id = u.id
				JOIN role r ON r.id = ur.role_id AND r.institution_id = u.institution_id
				WHERE u.institution_id = CAST(:inst AS uuid) AND u.active AND r.code = :admin
				  AND u.id <> CAST(:id AS uuid)
				"""
			),
			{"inst": institution_id, "admin": ADMIN, "id": user_id},
		).scalar_one()

	def guard_administrators(connection, user, *, will_be_active: bool, keeps_admin: bool,
							 new_institution: str) -> None:
		"""Self-protection and "at least one active administrator per institution"."""
		user_id, own = str(user["id"]), str(actor()["id"])
		if user_id == own and not will_be_active:
			raise _conflict("cannot_deactivate_self", "You cannot deactivate your own account", "active")
		if user_id == own and not keeps_admin:
			raise _conflict("cannot_remove_own_admin", "You cannot remove the administrator role from your own account",
							"role_codes")
		stays_admin = will_be_active and keeps_admin and new_institution == str(user["institution_id"])
		if stays_admin:
			return
		# One administrator change at a time: concurrent requests cannot remove the last two together.
		connection.execute(text("SELECT pg_advisory_xact_lock(hashtext(:key))"), {"key": ADMIN_LOCK_KEY})
		if is_effective_admin(connection, user_id) and other_active_admins(connection, str(user["institution_id"]), user_id) == 0:
			raise _conflict("last_admin", "At least one active administrator must remain", "active")

	def write_roles(connection, user_id: str, wanted: dict[str, str]) -> bool:
		current = {str(row[0]) for row in connection.execute(
			text("SELECT role_id FROM user_role WHERE user_id = CAST(:id AS uuid)"), {"id": user_id})}
		target = set(wanted.values())
		for role_id in current - target:
			connection.execute(text("DELETE FROM user_role WHERE user_id = CAST(:u AS uuid) AND role_id = CAST(:r AS uuid)"),
							   {"u": user_id, "r": role_id})
		for role_id in target - current:
			connection.execute(text("INSERT INTO user_role (user_id, role_id, assigned_at) "
									"VALUES (CAST(:u AS uuid), CAST(:r AS uuid), now())"), {"u": user_id, "r": role_id})
		return current != target

	def email_conflict() -> ApiError:
		return _conflict("email_exists", "A user with this email already exists", "email")

	# --- User writes ---------------------------------------------------------------------------

	@api_bp.post("/users")
	@require_admin("user")
	def create_user():
		payload = parse_object("user_create_request", USER_CREATE_FIELDS)
		active = payload.get("active")
		active = True if active is None else active
		user_id = str(uuid.uuid4())
		try:
			with core.engine.begin() as connection:
				name, email, institution_id, roles = identity_values(connection, payload)
				password = _password(payload, "password", "confirm_password")
				if email_taken(connection, email):
					raise email_conflict()
				connection.execute(
					text(
						"""
						INSERT INTO "user" (id, name, email, password_hash, password_updated_at, institution_id, active,
						                    created_at, updated_at)
						VALUES (CAST(:id AS uuid), :name, :email, :hash, now(), CAST(:inst AS uuid), :active, now(), now())
						"""
					),
					{"id": user_id, "name": name, "email": email, "hash": generate_password_hash(password),
					 "inst": institution_id, "active": active},
				)
				write_roles(connection, user_id, roles)
				audit(connection, "CREATE_USER", "user", user_id)
				result = user_detail(connection, user_id)
		except IntegrityError:
			raise email_conflict() from None
		logger.info("user created id=%s by=%s", user_id, actor()["id"])
		return render(result, root="managed_user", status=201)

	@api_bp.put("/users/<user_id>")
	@require_admin("user")
	def update_user(user_id: str):
		user_id = existing_user_id(user_id)
		payload = parse_object("user_update_request", USER_FIELDS)
		revoke = False
		try:
			with core.engine.begin() as connection:
				current = load_user(connection, user_id, for_update=True)
				name, email, institution_id, roles = identity_values(
					connection, payload, current_institution=str(current["institution_id"]))
				active = bool(current["active"]) if payload.get("active") is None else payload["active"]
				guard_administrators(connection, current, will_be_active=active, keeps_admin=ADMIN in roles,
									 new_institution=institution_id)
				if email_taken(connection, email, except_id=user_id):
					raise email_conflict()
				changed = (current["name"], current["email"], str(current["institution_id"])) != (name, email, institution_id)
				connection.execute(
					text('UPDATE "user" SET name = :name, email = :email, institution_id = CAST(:inst AS uuid), '
						 "active = :active, updated_at = now() WHERE id = CAST(:id AS uuid)"),
					{"name": name, "email": email, "inst": institution_id, "active": active, "id": user_id},
				)
				roles_changed = write_roles(connection, user_id, roles)
				if changed:
					audit(connection, "UPDATE_USER", "user", user_id)
				if bool(current["active"]) != active:
					audit(connection, f"{'ACTIVATE' if active else 'DEACTIVATE'}_USER", "user", user_id)
				if roles_changed:
					audit(connection, "CHANGE_USER_ROLES", "user", user_id)
				result = user_detail(connection, user_id)
				revoke = (not active and bool(current["active"])) or institution_id != str(current["institution_id"])
		except IntegrityError:
			raise email_conflict() from None
		if revoke:
			logger.info("sessions revoked user=%s count=%s", user_id, revoke_sessions(user_id))
		return render(result, root="managed_user")

	@api_bp.put("/users/<user_id>/roles")
	@require_admin("user")
	def assign_roles(user_id: str):
		user_id = existing_user_id(user_id)
		payload = parse_object("user_roles_request", ROLES_FIELDS)
		with core.engine.begin() as connection:
			current = load_user(connection, user_id, for_update=True)
			codes = list(dict.fromkeys(code.strip() for code in (payload.get("role_codes") or []) if code and code.strip()))
			if not codes:
				raise _invalid("roles_required", "Assign at least one role", "role_codes")
			roles = role_ids(connection, str(current["institution_id"]), codes)
			guard_administrators(connection, current, will_be_active=bool(current["active"]), keeps_admin=ADMIN in roles,
								 new_institution=str(current["institution_id"]))
			if write_roles(connection, user_id, roles):
				connection.execute(text('UPDATE "user" SET updated_at = now() WHERE id = CAST(:id AS uuid)'), {"id": user_id})
				audit(connection, "CHANGE_USER_ROLES", "user", user_id)
			result = user_detail(connection, user_id)
		return render(result, root="managed_user")

	def set_status(user_id: str, active: bool):
		user_id = existing_user_id(user_id)
		with core.engine.begin() as connection:
			current = load_user(connection, user_id, for_update=True)
			# A status-only change never touches roles.
			guard_administrators(connection, current, will_be_active=active, keeps_admin=True,
								 new_institution=str(current["institution_id"]))
			if bool(current["active"]) != active:
				connection.execute(text('UPDATE "user" SET active = :active, updated_at = now() WHERE id = CAST(:id AS uuid)'),
								   {"active": active, "id": user_id})
				audit(connection, f"{'ACTIVATE' if active else 'DEACTIVATE'}_USER", "user", user_id)
			result = user_detail(connection, user_id)
		if not active:
			logger.info("sessions revoked user=%s count=%s", user_id, revoke_sessions(user_id))
		return render(result, root="managed_user")

	@api_bp.post("/users/<user_id>/deactivate")
	@require_admin("user")
	def deactivate_user(user_id: str):
		return set_status(user_id, False)

	@api_bp.post("/users/<user_id>/activate")
	@require_admin("user")
	def activate_user(user_id: str):
		return set_status(user_id, True)

	@api_bp.post("/users/<user_id>/password")
	@require_admin("user")
	def reset_password(user_id: str):
		"""Administrator sets a new password: every session of that user ends (theirs must sign in again)."""
		user_id = existing_user_id(user_id)
		payload = parse_object("password_reset_request", RESET_FIELDS)
		with core.engine.begin() as connection:
			load_user(connection, user_id, for_update=True)
			password = _password(payload, "new_password", "confirm_new_password")
			connection.execute(
				text('UPDATE "user" SET password_hash = :hash, password_updated_at = now(), updated_at = now() '
					 "WHERE id = CAST(:id AS uuid)"),
				{"hash": generate_password_hash(password), "id": user_id},
			)
			audit(connection, "RESET_USER_PASSWORD", "user", user_id)
			result = user_detail(connection, user_id)
		# An administrator resetting their own password keeps the request's own access token.
		keep = frozenset({g.api_claims["jti"]}) if user_id == str(actor()["id"]) else frozenset()
		logger.info("sessions revoked user=%s count=%s", user_id, revoke_sessions(user_id, keep))
		return render(result, root="managed_user")

	@api_bp.post("/password/change")
	@require_bearer
	def change_own_password():
		"""Any authenticated user: current password + new one; other sessions of the account end."""
		payload = parse_object("password_change_request", CHANGE_FIELDS)
		user_id = str(actor()["id"])
		with core.engine.begin() as connection:
			stored = connection.execute(text('SELECT password_hash FROM "user" WHERE id = CAST(:id AS uuid) FOR UPDATE'),
										{"id": user_id}).first()
			if not stored or not check_password_hash(stored[0], payload.get("current_password") or ""):
				raise _invalid("invalid_current_password", "The current password is not correct", "current_password")
			password = _password(payload, "new_password", "confirm_new_password")
			connection.execute(
				text('UPDATE "user" SET password_hash = :hash, password_updated_at = now(), updated_at = now() '
					 "WHERE id = CAST(:id AS uuid)"),
				{"hash": generate_password_hash(password), "id": user_id},
			)
			audit(connection, "CHANGE_OWN_PASSWORD", "user", user_id)
		revoke_sessions(user_id, frozenset({g.api_claims["jti"]}))
		return "", 204

	# --- Roles -----------------------------------------------------------------------------------

	ROLE_SELECT = """
		SELECT r.id, r.code, r.description, r.institution_id, i.name AS institution_name,
		       (SELECT count(*) FROM user_role ur WHERE ur.role_id = r.id) AS assigned_users
		FROM role r
		JOIN institution i ON i.id = r.institution_id
	"""

	def role_document(row) -> dict[str, Any]:
		return {
			"id": str(row["id"]),
			"code": row["code"],
			"description": row["description"],
			"institution": {"id": str(row["institution_id"]), "name": row["institution_name"]},
			"assigned_users": int(row["assigned_users"]),
			"system": row["code"] in SYSTEM_ROLES,
		}

	def load_role(connection, raw_id: str, *, for_update: bool = False):
		role_id = _uuid(raw_id)
		row = connection.execute(
			text(ROLE_SELECT + " WHERE r.id = CAST(:id AS uuid)" + (" FOR UPDATE OF r" if for_update else "")),
			{"id": role_id},
		).mappings().first() if role_id else None
		if row is None:
			raise _not_found("role_not_found")
		return row

	@api_bp.get("/roles")
	@require_admin("role")
	def list_roles():
		with core.engine.connect() as connection:
			rows = connection.execute(text(ROLE_SELECT + " ORDER BY r.code, i.name")).mappings().all()
		return render([role_document(row) for row in rows], root="managed_roles")

	@api_bp.get("/roles/<role_id>")
	@require_admin("role")
	def get_role(role_id: str):
		with core.engine.connect() as connection:
			return render(role_document(load_role(connection, role_id)), root="managed_role")

	@api_bp.post("/roles")
	@require_admin("role")
	def create_role():
		payload = parse_object("role_create_request", ROLE_CREATE_FIELDS)
		code = _text(payload, "code", required=True, max_length=64, label="Role code").lower()
		if not ROLE_CODE_RE.match(code):
			raise _invalid("invalid_role_code", "Role code may only contain lowercase letters, numbers and underscores",
						   "code")
		description = _text(payload, "description", required=True, max_length=255, label="Description")
		role_id = str(uuid.uuid4())
		conflict = _conflict("role_code_exists", "A role with this code already exists for the institution", "code")
		try:
			with core.engine.begin() as connection:
				if connection.execute(text("SELECT 1 FROM role WHERE institution_id = CAST(:i AS uuid) AND code = :c"),
									  {"i": str(actor()["institution_id"]), "c": code}).first():
					raise conflict
				connection.execute(
					text("INSERT INTO role (id, code, description, institution_id) "
						 "VALUES (CAST(:id AS uuid), :code, :description, CAST(:inst AS uuid))"),
					{"id": role_id, "code": code, "description": description, "inst": str(actor()["institution_id"])},
				)
				audit(connection, "CREATE_ROLE", "role", role_id)
				result = role_document(load_role(connection, role_id))
		except IntegrityError:
			raise conflict from None
		return render(result, root="managed_role", status=201)

	@api_bp.put("/roles/<role_id>")
	@require_admin("role")
	def update_role(role_id: str):
		payload = parse_object("role_update_request", ROLE_UPDATE_FIELDS)
		with core.engine.begin() as connection:
			current = load_role(connection, role_id, for_update=True)
			description = _text(payload, "description", required=True, max_length=255, label="Description")
			if description != current["description"]:
				connection.execute(text("UPDATE role SET description = :d WHERE id = CAST(:id AS uuid)"),
								   {"d": description, "id": str(current["id"])})
				audit(connection, "UPDATE_ROLE", "role", current["id"])
			result = role_document(load_role(connection, str(current["id"])))
		return render(result, root="managed_role")

	@api_bp.delete("/roles/<role_id>")
	@require_admin("role")
	def delete_role(role_id: str):
		with core.engine.begin() as connection:
			current = load_role(connection, role_id, for_update=True)
			if current["code"] in SYSTEM_ROLES:
				raise _conflict("system_role", "System roles cannot be deleted")
			if int(current["assigned_users"]):
				raise _conflict("role_in_use", "Remove this role from every user before deleting it")
			audit(connection, "DELETE_ROLE", "role", current["id"])
			connection.execute(text("DELETE FROM role WHERE id = CAST(:id AS uuid)"), {"id": str(current["id"])})
		return render({"deleted": True, "id": str(current["id"])}, root="role_deleted")


def _client_ip(value: str | None) -> str | None:
	"""INET column: only a syntactically valid address is stored (never a free-form header)."""
	try:
		return str(ipaddress.ip_address((value or "").strip()))
	except ValueError:
		return None
