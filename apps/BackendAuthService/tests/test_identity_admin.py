"""Identity administration (PEF-DS09 §1.3.2 complement v0.1.4): users, roles, role assignment, passwords.

Real PostgreSQL (rolled-back transaction) and Redis (tracked keys deleted); see conftest.py.
XML bodies and responses are checked against schemas/identity.xsd.
"""

from __future__ import annotations

import uuid
from datetime import timedelta
from pathlib import Path

import pytest
from lxml import etree
from sqlalchemy import text

from conftest import PASSWORD

API = "/api/v1/auth"
XSD = etree.XMLSchema(etree.parse(str(Path(__file__).resolve().parents[1] / "schemas" / "identity.xsd")))
XML = {"Content-Type": "application/xml", "Accept": "application/xml"}
NEW_PASSWORD = "Brand-New-Secret-7"


def bearer(token):
	return {"Authorization": f"Bearer {token}"}


def login(client, email, password=PASSWORD):
	return client.post(f"{API}/login", json={"email": email, "password": password})


def access_for(client, world, key):
	response = login(client, world.emails[key])
	assert response.status_code == 200, response.get_data(as_text=True)
	return response.get_json()["access_token"]


@pytest.fixture
def admin(client, world):
	return bearer(access_for(client, world, "admin_a"))


def audit_actions(db_conn, resource_id) -> list[str]:
	"""Administration actions on the resource (sign-in events are checked in test_sign_in_audit.py)."""
	return db_conn.execute(text("SELECT action FROM access_audit WHERE resource_id = CAST(:id AS uuid) "
								"AND action NOT IN ('LOGIN', 'LOGIN_FAILED', 'LOGOUT') ORDER BY action"),
						   {"id": str(resource_id)}).scalars().all()


def new_user_body(world, **overrides):
	body = {"name": "New Person", "email": f"new.{world.tag}@auth-test.invalid", "institution_id": str(world.institution_a),
			"role_codes": ["station_operator"], "active": True, "password": NEW_PASSWORD, "confirm_password": NEW_PASSWORD}
	body.update(overrides)
	return body


def reason(response):
	return response.get_json()["details"].get("reason")


def assert_no_secrets(response):
	body = response.get_data(as_text=True).lower()
	for forbidden in ("password_hash", "pbkdf2", "scrypt", NEW_PASSWORD.lower(), PASSWORD.lower(), "refresh_token"):
		assert forbidden not in body


@pytest.fixture
def created(client, world, admin):
	response = client.post(f"{API}/users", json=new_user_body(world), headers=admin)
	assert response.status_code == 201, response.get_data(as_text=True)
	return response.get_json()


# --- Access control ------------------------------------------------------------------------------


@pytest.mark.parametrize("key", ["operator_a", "supervisor_a", "foreign_a"])
def test_only_effective_it_admin_manages_identities(client, world, db_conn, key):
	headers = bearer(access_for(client, world, key))
	target = world.users["operator_a"]
	assert client.get(f"{API}/users", headers=headers).status_code == 403
	response = client.post(f"{API}/users/{target}/deactivate", headers=headers)
	assert response.status_code == 403 and response.get_json()["error"] == "forbidden"
	assert client.get(f"{API}/roles", headers=headers).status_code == 403
	assert "DENIED_POST_USER" in audit_actions(db_conn, target)
	assert db_conn.execute(text('SELECT active FROM "user" WHERE id = :id'), {"id": target}).scalar_one() is True


def test_tokens_expired_invalid_revoked_or_missing_are_401(client, world, mint):
	user, inst = world.users["admin_a"], world.institution_a
	cases = [
		{},
		bearer("not-a-jwt"),
		bearer(mint(user, inst, lifetime=timedelta(seconds=-5))),
		bearer(mint(user, inst, revoked=True)),
		bearer(mint(user, inst, register=False)),
		bearer(mint(user, inst, key="another-secret-key-0123456789abcdef")),
		{"X-User-Role": "it_admin", "X-User-Id": str(user)},   # headers are never identity
	]
	for headers in cases:
		response = client.get(f"{API}/users", headers=headers)
		assert response.status_code == 401 and response.get_json()["error"] == "unauthorized"


# --- Users -------------------------------------------------------------------------------------------


def test_create_user_then_sign_in(client, world, admin, db_conn, created):
	assert created["email"] == f"new.{world.tag}@auth-test.invalid"
	assert created["institution"]["id"] == str(world.institution_a) and created["active"] is True
	assert [role["code"] for role in created["roles"]] == ["station_operator"]
	assert set(created) == {"id", "name", "email", "active", "institution", "roles", "last_login_at",
							"password_updated_at", "created_at", "updated_at"}
	stored = db_conn.execute(text('SELECT password_hash FROM "user" WHERE id = :id'), {"id": created["id"]}).scalar_one()
	assert stored and NEW_PASSWORD not in stored
	assert audit_actions(db_conn, created["id"]) == ["CREATE_USER"]
	session = login(client, created["email"], NEW_PASSWORD)
	assert session.status_code == 200 and session.get_json()["user"]["roles"] == ["station_operator"]
	# The portal signs in through the legacy cookie route: same account, same rules.
	assert client.post("/login", json={"email": created["email"].upper(), "password": NEW_PASSWORD}).status_code == 200


def test_list_and_detail_never_expose_secrets(client, world, admin, created):
	listing = client.get(f"{API}/users", headers=admin)
	assert listing.status_code == 200 and created["id"] in {user["id"] for user in listing.get_json()}
	assert_no_secrets(listing)
	detail = client.get(f"{API}/users/{created['id']}", headers=admin)
	assert detail.get_json() == {**created, "last_login_at": detail.get_json()["last_login_at"]}
	assert_no_secrets(detail)


@pytest.mark.parametrize("overrides, status, expected", [
	({"name": "  "}, 422, "name_required"),
	({"name": "x" * 161}, 422, "name_too_long"),
	({"email": ""}, 422, "email_required"),
	({"email": "not-an-email"}, 422, "invalid_email"),
	({"institution_id": ""}, 422, "institution_required"),
	({"institution_id": "nope"}, 422, "invalid_institution"),
	({"institution_id": str(uuid.uuid4())}, 422, "invalid_institution"),
	({"role_codes": []}, 422, "roles_required"),
	({"role_codes": ["it_admin", "ghost_role"]}, 422, "invalid_roles"),
	({"password": "short", "confirm_password": "short"}, 422, "password_too_short"),
	({"confirm_password": "something-else"}, 422, "password_mismatch"),
])
def test_create_user_validations(client, world, admin, db_conn, overrides, status, expected):
	response = client.post(f"{API}/users", json=new_user_body(world, **overrides), headers=admin)
	assert response.status_code == status and reason(response) == expected, response.get_data(as_text=True)
	assert_no_secrets(response)
	assert not db_conn.execute(text('SELECT 1 FROM "user" WHERE email = :e'), {"e": f"new.{world.tag}@auth-test.invalid"}).first()


def test_create_user_institution_rules(client, world, admin):
	inactive = client.post(f"{API}/users", json=new_user_body(world, institution_id=str(world.institution_off)), headers=admin)
	assert inactive.status_code == 422 and reason(inactive) == "institution_inactive"
	# Roles must belong to the chosen institution (B has no spd_supervisor role).
	foreign = client.post(f"{API}/users", json=new_user_body(world, institution_id=str(world.institution_b),
															   role_codes=["spd_supervisor"]), headers=admin)
	assert foreign.status_code == 422 and reason(foreign) == "invalid_roles"


def test_duplicate_email_is_409_case_insensitive(client, world, admin):
	response = client.post(f"{API}/users", json=new_user_body(world, email=world.emails["operator_a"].upper()), headers=admin)
	assert response.status_code == 409 and reason(response) == "email_exists"
	assert response.get_json()["error"] == "conflict"


@pytest.mark.parametrize("body, details_key", [
	({"name": "x", "password_hash": "x"}, "unknown_fields"),
	({"name": 5}, "field"),
	({"role_codes": "it_admin"}, "field"),
	({"active": "yes"}, "field"),
])
def test_malformed_bodies_are_400(client, world, admin, body, details_key):
	response = client.post(f"{API}/users", json=body, headers=admin)
	assert response.status_code == 400 and details_key in response.get_json()["details"]


def test_unknown_or_malformed_ids_are_404(client, world, admin):
	for path in (f"/users/{uuid.uuid4()}", "/users/not-a-uuid", f"/roles/{uuid.uuid4()}", "/roles/x"):
		response = client.get(f"{API}{path}", headers=admin)
		assert response.status_code == 404 and response.get_json()["error"] == "not_found"
	assert client.post(f"{API}/users/{uuid.uuid4()}/deactivate", headers=admin).status_code == 404


def test_update_user_and_role_changes_apply_to_the_next_request(client, world, admin, db_conn, created):
	user_token = bearer(login(client, created["email"], NEW_PASSWORD).get_json()["access_token"])
	body = {"name": "Renamed", "email": f"renamed.{world.tag}@auth-test.invalid", "institution_id": str(world.institution_a),
			"role_codes": ["spd_supervisor", "station_operator"], "active": True}
	response = client.put(f"{API}/users/{created['id']}", json=body, headers=admin)
	assert response.status_code == 200
	assert response.get_json()["name"] == "Renamed"
	assert [role["code"] for role in response.get_json()["roles"]] == ["spd_supervisor", "station_operator"]
	assert audit_actions(db_conn, created["id"]) == ["CHANGE_USER_ROLES", "CREATE_USER", "UPDATE_USER"]
	# Same token, no new login: roles come from PostgreSQL on every request.
	assert client.get(f"{API}/permissions", headers=user_token).get_json()["roles"] == ["spd_supervisor", "station_operator"]
	assigned = client.put(f"{API}/users/{created['id']}/roles", json={"role_codes": ["it_admin"]}, headers=admin)
	assert assigned.status_code == 200 and [r["code"] for r in assigned.get_json()["roles"]] == ["it_admin"]
	assert client.get(f"{API}/permissions", headers=user_token).get_json()["roles"] == ["it_admin"]
	assert client.get(f"{API}/users", headers=user_token).status_code == 200   # now an administrator
	client.put(f"{API}/users/{created['id']}/roles", json={"role_codes": ["station_operator"]}, headers=admin)
	assert client.get(f"{API}/users", headers=user_token).status_code == 403   # and no longer


def test_deactivation_ends_sessions_and_reactivation_allows_login(client, world, admin, db_conn, store, created):
	session = login(client, created["email"], NEW_PASSWORD).get_json()
	access, refresh = session["access_token"], session["refresh_token"]
	assert client.get(f"{API}/me", headers=bearer(access)).status_code == 200
	response = client.post(f"{API}/users/{created['id']}/deactivate", headers=admin)
	assert response.status_code == 200 and response.get_json()["active"] is False
	# The very next request is refused, and both Redis sessions are marked revoked.
	assert client.get(f"{API}/me", headers=bearer(access)).status_code == 401
	assert client.post(f"{API}/refresh", json={"refresh_token": refresh}).status_code == 401
	jtis = store.client.smembers(store._user_tokens_key(created["id"]))
	assert not jtis  # index emptied
	assert login(client, created["email"], NEW_PASSWORD).status_code == 401
	reactivated = client.post(f"{API}/users/{created['id']}/activate", headers=admin)
	assert reactivated.status_code == 200 and reactivated.get_json()["active"] is True
	assert client.get(f"{API}/me", headers=bearer(access)).status_code == 401  # old sessions stay revoked
	assert login(client, created["email"], NEW_PASSWORD).status_code == 200
	assert audit_actions(db_conn, created["id"]) == ["ACTIVATE_USER", "CREATE_USER", "DEACTIVATE_USER"]


def test_revocation_is_recorded_in_redis(client, world, admin, store, created):
	access = login(client, created["email"], NEW_PASSWORD).get_json()["access_token"]
	import jwt as pyjwt
	jti = pyjwt.decode(access, options={"verify_signature": False})["jti"]
	assert store.get_token(jti)["revoked"] is False
	client.post(f"{API}/users/{created['id']}/deactivate", headers=admin)
	assert store.get_token(jti)["revoked"] is True


def test_admin_password_reset_revokes_sessions(client, world, admin, db_conn, created):
	access = login(client, created["email"], NEW_PASSWORD).get_json()["access_token"]
	short = client.post(f"{API}/users/{created['id']}/password", json={"new_password": "short"}, headers=admin)
	assert short.status_code == 422 and reason(short) == "password_too_short"
	mismatch = client.post(f"{API}/users/{created['id']}/password",
						   json={"new_password": "Another-Pass-1", "confirm_new_password": "x"}, headers=admin)
	assert mismatch.status_code == 422 and reason(mismatch) == "password_mismatch"
	response = client.post(f"{API}/users/{created['id']}/password",
						   json={"new_password": "Another-Pass-1", "confirm_new_password": "Another-Pass-1"}, headers=admin)
	assert response.status_code == 200 and response.get_json()["password_updated_at"]
	assert_no_secrets(response)
	assert client.get(f"{API}/me", headers=bearer(access)).status_code == 401
	assert login(client, created["email"], NEW_PASSWORD).status_code == 401
	assert login(client, created["email"], "Another-Pass-1").status_code == 200
	assert "RESET_USER_PASSWORD" in audit_actions(db_conn, created["id"])


def test_own_password_change(client, world, db_conn):
	first = login(client, world.emails["operator_a"]).get_json()["access_token"]
	second = login(client, world.emails["operator_a"]).get_json()["access_token"]
	wrong = client.post(f"{API}/password/change", json={"current_password": "nope", "new_password": NEW_PASSWORD},
						headers=bearer(first))
	assert wrong.status_code == 422 and reason(wrong) == "invalid_current_password"
	ok = client.post(f"{API}/password/change", json={"current_password": PASSWORD, "new_password": NEW_PASSWORD},
					 headers=bearer(first))
	assert ok.status_code == 204
	assert client.get(f"{API}/me", headers=bearer(first)).status_code == 200    # this session continues
	assert client.get(f"{API}/me", headers=bearer(second)).status_code == 401   # every other one ends
	assert login(client, world.emails["operator_a"], NEW_PASSWORD).status_code == 200
	assert audit_actions(db_conn, world.users["operator_a"]) == ["CHANGE_OWN_PASSWORD"]


# --- Administrator protection ------------------------------------------------------------------------


def test_self_protection(client, world, admin):
	me = world.users["admin_a"]
	response = client.post(f"{API}/users/{me}/deactivate", headers=admin)
	assert response.status_code == 409 and reason(response) == "cannot_deactivate_self"
	response = client.put(f"{API}/users/{me}/roles", json={"role_codes": ["station_operator"]}, headers=admin)
	assert response.status_code == 409 and reason(response) == "cannot_remove_own_admin"


def test_the_last_active_administrator_of_an_institution_is_kept(client, world, admin, db_conn):
	only_b = client.post(f"{API}/users", json=new_user_body(world, email=f"admin.b.{world.tag}@auth-test.invalid",
															  institution_id=str(world.institution_b),
															  role_codes=["it_admin"]), headers=admin)
	assert only_b.status_code == 201
	user_id = only_b.get_json()["id"]
	for call in (
		lambda: client.post(f"{API}/users/{user_id}/deactivate", headers=admin),
		lambda: client.put(f"{API}/users/{user_id}/roles", json={"role_codes": ["station_operator"]}, headers=admin),
		lambda: client.put(f"{API}/users/{user_id}", headers=admin, json={
			"name": "B", "email": f"admin.b.{world.tag}@auth-test.invalid", "institution_id": str(world.institution_a),
			"role_codes": ["it_admin"], "active": True}),
	):
		response = call()
		assert response.status_code == 409 and reason(response) == "last_admin", response.get_data(as_text=True)
	assert db_conn.execute(text('SELECT active FROM "user" WHERE id = :id'), {"id": user_id}).scalar_one() is True
	# Institution A has two administrators: one of them can be deactivated.
	assert client.post(f"{API}/users/{world.users['multi_a']}/deactivate", headers=admin).status_code == 200


# --- Roles ----------------------------------------------------------------------------------------


def test_roles_lifecycle_and_protection(client, world, admin, db_conn):
	listing = client.get(f"{API}/roles", headers=admin).get_json()
	admin_role = next(r for r in listing if r["code"] == "it_admin" and r["institution"]["id"] == str(world.institution_a))
	assert admin_role["system"] is True and admin_role["assigned_users"] == 2
	code = f"auditor_{world.tag}"
	created = client.post(f"{API}/roles", json={"code": code.upper(), "description": "Auditor"}, headers=admin)
	assert created.status_code == 201 and created.get_json()["code"] == code
	assert created.get_json()["institution"]["id"] == str(world.institution_a) and created.get_json()["system"] is False
	role_id = created.get_json()["id"]
	duplicate = client.post(f"{API}/roles", json={"code": code, "description": "Again"}, headers=admin)
	assert duplicate.status_code == 409 and reason(duplicate) == "role_code_exists"
	bad = client.post(f"{API}/roles", json={"code": "9 bad", "description": "x"}, headers=admin)
	assert bad.status_code == 422 and reason(bad) == "invalid_role_code"
	assert reason(client.post(f"{API}/roles", json={"code": "ok_code"}, headers=admin)) == "description_required"
	updated = client.put(f"{API}/roles/{role_id}", json={"description": "Read-only auditor"}, headers=admin)
	assert updated.status_code == 200 and updated.get_json()["description"] == "Read-only auditor"
	assert client.put(f"{API}/roles/{role_id}", json={"code": "other"}, headers=admin).status_code == 400  # code is immutable
	system = client.delete(f"{API}/roles/{admin_role['id']}", headers=admin)
	assert system.status_code == 409 and reason(system) == "system_role"
	client.put(f"{API}/users/{world.users['operator_a']}/roles", json={"role_codes": ["station_operator", code]}, headers=admin)
	in_use = client.delete(f"{API}/roles/{role_id}", headers=admin)
	assert in_use.status_code == 409 and reason(in_use) == "role_in_use"
	client.put(f"{API}/users/{world.users['operator_a']}/roles", json={"role_codes": ["station_operator"]}, headers=admin)
	deleted = client.delete(f"{API}/roles/{role_id}", headers=admin)
	assert deleted.status_code == 200 and deleted.get_json() == {"deleted": True, "id": role_id}
	assert audit_actions(db_conn, role_id) == ["CREATE_ROLE", "DELETE_ROLE", "UPDATE_ROLE"]


# --- XML / XSD -----------------------------------------------------------------------------------------


def test_create_user_xml_and_responses_conform_to_the_xsd(client, world, admin):
	body = (f"<user_create_request><name>Xml Person</name><email>xml.{world.tag}@auth-test.invalid</email>"
			f"<institution_id>{world.institution_a}</institution_id><role_codes><role_code>spd_supervisor</role_code>"
			f"</role_codes><active>1</active><password>{NEW_PASSWORD}</password></user_create_request>").encode()
	assert XSD.validate(etree.fromstring(body))
	response = client.post(f"{API}/users", data=body, headers={**admin, **XML})
	assert response.status_code == 201, response.get_data(as_text=True)
	document = etree.fromstring(response.data)
	XSD.assertValid(document)
	assert document.findtext("roles/role/code") == "spd_supervisor"
	assert_no_secrets(response)
	for path in ("/users", f"/users/{document.findtext('id')}", "/roles"):
		XSD.assertValid(etree.fromstring(client.get(f"{API}{path}", headers={**admin, "Accept": "application/xml"}).data))


@pytest.mark.parametrize("body", [
	b"<user_create_request><name>x</name><password_hash>x</password_hash></user_create_request>",
	b"<user_create_request><name lang='en'>x</name></user_create_request>",
	b"<user_create_request>text<name>x</name></user_create_request>",
	b"<user_roles_request><role_codes><role>it_admin</role></role_codes></user_roles_request>",
])
def test_invalid_xml_bodies_are_400_with_documented_errors(client, world, admin, body):
	root = etree.fromstring(body).tag
	path = f"/users/{world.users['operator_a']}/roles" if root == "user_roles_request" else "/users"
	send = client.put if root == "user_roles_request" else client.post
	response = send(f"{API}{path}", data=body, headers={**admin, **XML})
	assert response.status_code == 400
	document = etree.fromstring(response.data)
	XSD.assertValid(document)
	assert document.findtext("error") == "invalid_request"


def test_xsd_failure_details(client, world, admin):
	body = b"<user_create_request><name lang='en'>x</name></user_create_request>"
	payload = client.post(f"{API}/users", data=body, headers={**admin, "Content-Type": "application/xml"}).get_json()
	assert payload["details"]["reason"] == "xsd_validation_failed"
	assert payload["details"]["schema"] == f"{API}/schemas/identity.xsd" and payload["details"]["errors"][0]["message"]


def test_json_and_xml_give_the_same_business_reason(client, world, admin):
	xml = (f"<user_create_request><name>n</name><email>bad</email><institution_id>{world.institution_a}</institution_id>"
		   "</user_create_request>").encode()
	from_xml = client.post(f"{API}/users", data=xml, headers={**admin, "Content-Type": "application/xml"})
	from_json = client.post(f"{API}/users", json={"name": "n", "email": "bad", "institution_id": str(world.institution_a)},
							headers=admin)
	assert from_xml.status_code == from_json.status_code == 422
	assert reason(from_xml) == reason(from_json) == "invalid_email"


# --- Shared token rules used by the portal (legacy routes) -------------------------------------------


def test_portal_verify_uses_only_roles_of_the_users_institution(client, world):
	client.post("/login", json={"email": world.emails["foreign_a"], "password": PASSWORD})
	roles = [role["code"] for role in client.get("/verify").get_json()["user"]["roles"]]
	assert roles == ["station_operator"]  # the it_admin role of institution B is not effective in A


def test_portal_verify_rejects_a_deactivated_institution(client, world, db_conn):
	assert client.post("/login", json={"email": world.emails["operator_a"], "password": PASSWORD}).status_code == 200
	assert client.get("/verify").status_code == 200
	db_conn.execute(text("UPDATE institution SET active = FALSE WHERE id = :id"), {"id": world.institution_a})
	assert client.get("/verify").status_code == 401


def test_contract_documents_are_served(client):
	assert b"/api/v1/auth/users" in client.get(f"{API}/openapi.yaml").data
	assert client.get(f"{API}/schemas/identity.xsd").data == (Path(__file__).resolve().parents[1] / "schemas" / "identity.xsd").read_bytes()
	assert b"swagger-ui" in client.get(f"{API}/docs").data


def test_public_self_registration_is_disabled_by_default(client, world, db_conn):
	email = f"self.{world.tag}@auth-test.invalid"
	response = client.post("/register", json={"name": "Self", "email": email, "password": NEW_PASSWORD,
											   "institution_id": str(world.institution_a)})
	assert response.status_code == 404
	assert not db_conn.execute(text('SELECT 1 FROM "user" WHERE email = :e'), {"e": email}).first()
