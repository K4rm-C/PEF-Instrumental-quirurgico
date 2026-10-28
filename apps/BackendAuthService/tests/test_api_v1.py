"""/api/v1/auth/* (PEF-DS09 §1.3.2) and DS09 health checks — integration tests.

Uses real PostgreSQL (rolled-back transaction) and Redis (tracked keys deleted); see conftest.py.
"""

from __future__ import annotations

import json
import logging
import uuid
import xml.etree.ElementTree as ET
from datetime import timedelta

import jwt
import pytest
from sqlalchemy import text

import main
from api_format import CorrelationIdFilter
from conftest import PASSWORD

API = "/api/v1/auth"
# HS384/HS512 tokens are forged on purpose with the HS256 test secret.
pytestmark = pytest.mark.filterwarnings("ignore:The HMAC key is")
LOGIN_FIELDS = ["access_token", "refresh_token", "token_type", "expires_in", "user"]
USER_FIELDS = ["id", "name", "email", "institution_id", "ui_preferences", "roles"]


def login(client, world, key, password=PASSWORD, **kwargs):
	return client.post(f"{API}/login", json={"email": world.emails[key], "password": password}, **kwargs)


def bearer(token):
	return {"Authorization": f"Bearer {token}"}


def tokens_for(client, world, key):
	body = login(client, world, key).get_json()
	return body["access_token"], body["refresh_token"]


def assert_envelope(response, status, error):
	assert response.status_code == status, response.get_data(as_text=True)
	body = response.get_json()
	assert list(body) == ["error", "message", "correlation_id", "details"]
	assert body["error"] == error
	assert body["correlation_id"] == response.headers["X-Correlation-ID"]
	return body


# --- Login ------------------------------------------------------------------------------


def test_login_json_returns_ds09_fields(client, world):
	response = login(client, world, "operator_a")
	assert response.status_code == 200
	assert response.mimetype == "application/json"
	body = response.get_json()
	assert list(body) == LOGIN_FIELDS
	assert body["token_type"] == "Bearer"
	assert body["expires_in"] == main.app.config["OP_ACCESS_TOKEN_MINUTES"] * 60
	assert list(body["user"]) == USER_FIELDS
	assert body["user"] == {
		"id": str(world.users["operator_a"]),
		"name": "Test operator_a",
		"email": world.emails["operator_a"],
		"institution_id": str(world.institution_a),
		"ui_preferences": {"locale": "es-MX", "theme": "dark"},
		"roles": ["station_operator"],
	}
	# API clients get tokens in the body only; no cookies are set.
	assert "Set-Cookie" not in response.headers


def test_login_tokens_follow_redis_contract_used_by_catalog(client, world, store):
	"""The access token carries exactly what CatalogService/auth.py validates."""
	body = login(client, world, "operator_a").get_json()
	for raw, expected_type in ((body["access_token"], "access"), (body["refresh_token"], "refresh")):
		header = jwt.get_unverified_header(raw)
		assert header["alg"] == main.app.config["JWT_ALGORITHM"]
		claims = jwt.decode(raw, main.app.config["JWT_SECRET_KEY"], algorithms=[main.app.config["JWT_ALGORITHM"]])
		assert claims["type"] == expected_type
		assert claims["sub"] == str(world.users["operator_a"])
		stored = json.loads(store.client.get(f"auth:jwt:{claims['jti']}"))
		assert stored["token_type"] == expected_type
		assert stored["user_id"] == claims["sub"]
		assert stored["institution_id"] == str(world.institution_a)
		assert stored["revoked"] is False


def test_login_email_is_case_insensitive(client, world):
	response = client.post(f"{API}/login", json={"email": world.emails["operator_a"].upper(), "password": PASSWORD})
	assert response.status_code == 200


def test_login_xml(client, world):
	body = (
		f"<login_request><email>{world.emails['operator_a']}</email>"
		f"<password>{PASSWORD}</password></login_request>"
	)
	response = client.post(f"{API}/login", data=body,
						   headers={"Content-Type": "application/xml", "Accept": "application/xml"})
	assert response.status_code == 200
	assert response.mimetype == "application/xml"
	root = ET.fromstring(response.data)
	assert root.tag == "login_response"
	assert [child.tag for child in root] == LOGIN_FIELDS
	assert root.findtext("token_type") == "Bearer"
	user = root.find("user")
	assert [child.tag for child in user] == USER_FIELDS
	assert [role.text for role in user.find("roles")] == ["station_operator"]
	assert [r.tag for r in user.find("roles")] == ["role"]
	assert user.find("ui_preferences").findtext("locale") == "es-MX"


def test_login_json_and_xml_are_equivalent(client, world):
	as_json = login(client, world, "multi_a").get_json()
	xml_root = ET.fromstring(
		login(client, world, "multi_a", headers={"Accept": "application/xml"}).data
	)
	user = xml_root.find("user")
	as_xml_user = {
		"id": user.findtext("id"),
		"name": user.findtext("name"),
		"email": user.findtext("email"),
		"institution_id": user.findtext("institution_id"),
		"ui_preferences": {c.tag: c.text for c in user.find("ui_preferences")},
		"roles": [r.text for r in user.find("roles")],
	}
	assert as_xml_user == as_json["user"]
	assert int(xml_root.findtext("expires_in")) == as_json["expires_in"]


def test_login_wrong_password_is_401(client, world):
	assert_envelope(login(client, world, "operator_a", password="wrong-password"), 401, "invalid_credentials")


def test_login_unknown_email_is_401(client, world):
	response = client.post(f"{API}/login", json={"email": f"nobody.{world.tag}@auth-test.invalid", "password": "x"})
	assert_envelope(response, 401, "invalid_credentials")


def test_login_inactive_user_is_401(client, world):
	assert_envelope(login(client, world, "inactive_a"), 401, "invalid_credentials")


def test_login_inactive_institution_is_401(client, world):
	"""Same as legacy Auth: an unusable institution is treated as invalid credentials (DS01 rule 1)."""
	assert_envelope(login(client, world, "off_user"), 401, "invalid_credentials")


def test_login_rate_limited_is_429(client, world):
	for _ in range(main.token_store.login_fail_max):
		login(client, world, "operator_a", password="wrong-password")
	assert_envelope(login(client, world, "operator_a"), 429, "rate_limited")


@pytest.mark.parametrize(
	"payload, details",
	[
		({"email": "a@b.c"}, {"missing_fields": ["password"]}),
		({"email": "", "password": "x"}, {"missing_fields": ["email"]}),
		({"email": "a@b.c", "password": 123}, {"field": "password"}),
		({"email": "a@b.c", "password": "x", "institution_id": "y"}, {"unknown_fields": ["institution_id"]}),
	],
)
def test_login_invalid_body_is_400(client, world, payload, details):
	body = assert_envelope(client.post(f"{API}/login", json=payload), 400, "invalid_request")
	assert body["details"] == details


@pytest.mark.parametrize(
	"data, content_type",
	[("not json", "application/json"), ("[1]", "application/json"), ("email=x", "text/plain"),
	 ("<login_request><email>x</email>", "application/xml"), ("<other><email>x</email></other>", "application/xml"),
	 ('<!DOCTYPE x [<!ENTITY e "x">]><login_request><email>&e;</email><password>p</password></login_request>',
	  "application/xml")],
)
def test_login_malformed_body_is_400(client, world, data, content_type):
	assert_envelope(client.post(f"{API}/login", data=data, headers={"Content-Type": content_type}), 400, "invalid_request")


def test_login_error_never_echoes_password(client, world):
	response = login(client, world, "operator_a", password="Sup3r-Secret-Value")
	assert "Sup3r-Secret-Value" not in response.get_data(as_text=True)


def test_login_roles_exclude_other_institution(client, world):
	body = login(client, world, "foreign_a").get_json()
	assert body["user"]["roles"] == ["station_operator"]


# --- JWT validation (exact algorithm, expiry, Redis) ------------------------------------


@pytest.mark.parametrize("path", [f"{API}/me", f"{API}/permissions", "/verify"])
def test_configured_algorithm_is_accepted(client, world, mint, path):
	assert main.app.config["JWT_ALGORITHM"] == "HS256"
	token = mint(world.users["operator_a"], world.institution_a, algorithm="HS256")
	assert client.get(path, headers=bearer(token)).status_code == 200


@pytest.mark.parametrize("algorithm", ["HS384", "HS512", "none"])
@pytest.mark.parametrize("path", [f"{API}/me", f"{API}/permissions", "/verify"])
def test_other_algorithms_are_rejected(client, world, mint, algorithm, path):
	"""Same secret, valid claims and live Redis session: only the algorithm differs."""
	token = mint(world.users["operator_a"], world.institution_a, algorithm=algorithm)
	assert jwt.get_unverified_header(token)["alg"] == algorithm
	assert client.get(path, headers=bearer(token)).status_code == 401


@pytest.mark.parametrize("algorithm", ["HS384", "none"])
def test_refresh_rejects_other_algorithms(client, world, mint, algorithm):
	token = mint(world.users["operator_a"], world.institution_a, token_type="refresh", algorithm=algorithm)
	assert_envelope(client.post(f"{API}/refresh", json={"refresh_token": token}), 401, "unauthorized")


def test_expired_token_is_401(client, world, mint):
	token = mint(world.users["operator_a"], world.institution_a, lifetime=timedelta(seconds=-5))
	assert_envelope(client.get(f"{API}/me", headers=bearer(token)), 401, "unauthorized")


@pytest.mark.parametrize("header", ["Bearer not.a.jwt", "Bearer", "Basic abc", "abc"])
def test_malformed_token_is_401(client, world, header):
	assert_envelope(client.get(f"{API}/me", headers={"Authorization": header}), 401, "unauthorized")


def test_wrong_secret_is_401(client, world, mint):
	token = mint(world.users["operator_a"], world.institution_a, key="another-secret-0123456789abcdef-xyz")
	assert client.get(f"{API}/me", headers=bearer(token)).status_code == 401


def test_jti_missing_from_redis_is_401(client, world, mint):
	token = mint(world.users["operator_a"], world.institution_a, register=False)
	assert_envelope(client.get(f"{API}/me", headers=bearer(token)), 401, "unauthorized")


def test_revoked_token_is_401(client, world, mint):
	token = mint(world.users["operator_a"], world.institution_a, revoked=True)
	assert_envelope(client.get(f"{API}/me", headers=bearer(token)), 401, "unauthorized")


def test_refresh_token_is_not_an_access_token(client, world):
	_, refresh = tokens_for(client, world, "operator_a")
	assert_envelope(client.get(f"{API}/me", headers=bearer(refresh)), 401, "unauthorized")


def test_cookie_is_not_accepted_by_api(client, world):
	access, _ = tokens_for(client, world, "operator_a")
	client.set_cookie("access_token", access)
	assert_envelope(client.get(f"{API}/me"), 401, "unauthorized")


def test_session_institution_must_match_postgresql(client, world, mint):
	token = mint(world.users["operator_a"], world.institution_b)
	assert_envelope(client.get(f"{API}/me", headers=bearer(token)), 401, "unauthorized")


def test_user_deactivated_after_login_is_401(client, world, db_conn):
	access, _ = tokens_for(client, world, "operator_a")
	db_conn.execute(text('UPDATE "user" SET active = FALSE WHERE id = :id'), {"id": world.users["operator_a"]})
	assert client.get(f"{API}/me", headers=bearer(access)).status_code == 401


def test_institution_deactivated_after_login_is_401(client, world, db_conn):
	access, _ = tokens_for(client, world, "operator_a")
	db_conn.execute(text("UPDATE institution SET active = FALSE WHERE id = :id"), {"id": world.institution_a})
	assert client.get(f"{API}/me", headers=bearer(access)).status_code == 401


# --- Refresh ----------------------------------------------------------------------------


def test_refresh_returns_new_working_pair(client, world, store):
	access, refresh = tokens_for(client, world, "supervisor_a")
	response = client.post(f"{API}/refresh", json={"refresh_token": refresh})
	assert response.status_code == 200
	body = response.get_json()
	assert list(body) == ["access_token", "refresh_token", "token_type", "expires_in"]
	assert body["token_type"] == "Bearer"
	assert body["access_token"] != access and body["refresh_token"] != refresh
	assert client.get(f"{API}/me", headers=bearer(body["access_token"])).status_code == 200
	# Rotation: the presented refresh token is revoked and cannot be replayed.
	assert_envelope(client.post(f"{API}/refresh", json={"refresh_token": refresh}), 401, "unauthorized")
	# The new refresh token works.
	assert client.post(f"{API}/refresh", json={"refresh_token": body["refresh_token"]}).status_code == 200


def test_refresh_xml(client, world):
	_, refresh = tokens_for(client, world, "operator_a")
	response = client.post(
		f"{API}/refresh",
		data=f"<refresh_request><refresh_token>{refresh}</refresh_token></refresh_request>",
		headers={"Content-Type": "application/xml", "Accept": "application/xml"},
	)
	assert response.status_code == 200
	root = ET.fromstring(response.data)
	assert root.tag == "refresh_response"
	assert [c.tag for c in root] == ["access_token", "refresh_token", "token_type", "expires_in"]


def test_refresh_with_access_token_is_401(client, world):
	access, _ = tokens_for(client, world, "operator_a")
	assert_envelope(client.post(f"{API}/refresh", json={"refresh_token": access}), 401, "unauthorized")


def test_refresh_expired_or_revoked_is_401(client, world, mint):
	expired = mint(world.users["operator_a"], world.institution_a, token_type="refresh", lifetime=timedelta(seconds=-5))
	revoked = mint(world.users["operator_a"], world.institution_a, token_type="refresh", revoked=True)
	for token in (expired, revoked):
		assert_envelope(client.post(f"{API}/refresh", json={"refresh_token": token}), 401, "unauthorized")


def test_refresh_requires_body(client, world):
	assert_envelope(client.post(f"{API}/refresh", json={}), 400, "invalid_request")


def test_refresh_idle_timeout_still_applies(client, world, store, monkeypatch):
	"""Same policy as legacy refresh: an idle SPD session is refused and its token revoked."""
	_, refresh = tokens_for(client, world, "supervisor_a")
	monkeypatch.setattr(main, "_token_policy", lambda role: {"access": 900, "refresh": 3600, "idle": 1, "max_session": 3600})
	claims = jwt.decode(refresh, main.app.config["JWT_SECRET_KEY"], algorithms=["HS256"])
	claims["la"] -= 120
	stale = jwt.encode(claims, main.app.config["JWT_SECRET_KEY"], algorithm="HS256")
	body = assert_envelope(client.post(f"{API}/refresh", json={"refresh_token": stale}), 401, "unauthorized")
	assert "inactivity" in body["message"]
	assert json.loads(store.client.get(f"auth:jwt:{claims['jti']}"))["revoked"] is True


# --- Logout -----------------------------------------------------------------------------


def _session(store, token):
	"""Redis session of a token — exactly what CatalogService/auth.py reads."""
	jti = jwt.decode(token, main.app.config["JWT_SECRET_KEY"], algorithms=["HS256"])["jti"]
	return json.loads(store.client.get(f"auth:jwt:{jti}"))


def _assert_still_valid(client, access, refresh):
	assert client.get(f"{API}/me", headers=bearer(access)).status_code == 200
	assert client.post(f"{API}/refresh", json={"refresh_token": refresh}).status_code == 200


def test_logout_revokes_access_and_refresh(client, world, store):
	access, refresh = tokens_for(client, world, "operator_a")
	response = client.post(f"{API}/logout", headers=bearer(access), json={"refresh_token": refresh})
	assert response.status_code == 204
	assert response.get_data() == b""
	assert response.headers["X-Correlation-ID"]
	# Revoked in Redis: Catalog (same auth:jwt:{jti} contract) rejects the access token with 401.
	assert _session(store, access)["revoked"] is True
	assert _session(store, refresh)["revoked"] is True
	assert_envelope(client.get(f"{API}/me", headers=bearer(access)), 401, "unauthorized")
	assert_envelope(client.post(f"{API}/refresh", json={"refresh_token": refresh}), 401, "unauthorized")


def test_logout_xml(client, world, store):
	access, refresh = tokens_for(client, world, "operator_a")
	response = client.post(
		f"{API}/logout",
		headers={**bearer(access), "Content-Type": "application/xml", "Accept": "application/xml"},
		data=f"<logout_request><refresh_token>{refresh}</refresh_token></logout_request>",
	)
	assert response.status_code == 204
	assert _session(store, access)["revoked"] is True
	assert client.post(f"{API}/refresh", json={"refresh_token": refresh}).status_code == 401


@pytest.mark.parametrize(
	"kwargs, details",
	[
		({}, {"content_type": None}),
		({"json": {}}, {"missing_fields": ["refresh_token"]}),
		({"json": {"refresh_token": None}}, {"missing_fields": ["refresh_token"]}),
		({"json": {"refresh_token": ""}}, {"missing_fields": ["refresh_token"]}),
		({"data": "<logout_request/>", "headers": {"Content-Type": "application/xml"}}, {"missing_fields": ["refresh_token"]}),
	],
)
def test_logout_without_refresh_token_is_400_and_revokes_nothing(client, world, store, kwargs, details):
	access, refresh = tokens_for(client, world, "operator_a")
	headers = {**bearer(access), **kwargs.pop("headers", {})}
	body = assert_envelope(client.post(f"{API}/logout", headers=headers, **kwargs), 400, "invalid_request")
	assert body["details"] == details
	_assert_still_valid(client, access, refresh)


def test_logout_without_refresh_token_xml_error(client, world):
	access, _ = tokens_for(client, world, "operator_a")
	response = client.post(
		f"{API}/logout",
		headers={**bearer(access), "Content-Type": "application/xml", "Accept": "application/xml"},
		data="<logout_request/>",
	)
	assert response.status_code == 400
	root = ET.fromstring(response.data)
	assert root.tag == "error_response" and root.findtext("error") == "invalid_request"


def test_logout_with_other_users_refresh_is_403_and_revokes_nothing(client, world, store):
	access_a, refresh_a = tokens_for(client, world, "operator_a")
	_, refresh_other = tokens_for(client, world, "supervisor_a")
	response = client.post(f"{API}/logout", headers=bearer(access_a), json={"refresh_token": refresh_other})
	body = assert_envelope(response, 403, "forbidden")
	assert refresh_other not in response.get_data(as_text=True)
	assert body["details"] == {}
	assert _session(store, refresh_other)["revoked"] is False
	assert _session(store, access_a)["revoked"] is False
	assert client.post(f"{API}/refresh", json={"refresh_token": refresh_other}).status_code == 200
	_assert_still_valid(client, access_a, refresh_a)


@pytest.mark.parametrize("case", ["access_as_refresh", "revoked", "expired", "not_in_redis", "hs384", "garbage"])
def test_logout_with_invalid_refresh_is_401_and_revokes_nothing(client, world, mint, case):
	access, refresh = tokens_for(client, world, "operator_a")
	user, inst = world.users["operator_a"], world.institution_a
	bad = {
		"access_as_refresh": lambda: access,
		"revoked": lambda: mint(user, inst, token_type="refresh", revoked=True),
		"expired": lambda: mint(user, inst, token_type="refresh", lifetime=timedelta(seconds=-5)),
		"not_in_redis": lambda: mint(user, inst, token_type="refresh", register=False),
		"hs384": lambda: mint(user, inst, token_type="refresh", algorithm="HS384"),
		"garbage": lambda: "not.a.jwt",
	}[case]()
	response = client.post(f"{API}/logout", headers=bearer(access), json={"refresh_token": bad})
	assert_envelope(response, 401, "unauthorized")
	_assert_still_valid(client, access, refresh)


def test_logout_requires_valid_access_token(client, world):
	_, refresh = tokens_for(client, world, "operator_a")
	assert_envelope(client.post(f"{API}/logout", json={"refresh_token": refresh}), 401, "unauthorized")
	# A refresh token is not accepted as the Bearer credential.
	assert_envelope(client.post(f"{API}/logout", headers=bearer(refresh), json={"refresh_token": refresh}), 401, "unauthorized")
	access, refresh = tokens_for(client, world, "operator_a")
	assert client.post(f"{API}/logout", headers=bearer(access), json={"refresh_token": refresh}).status_code == 204
	assert_envelope(client.post(f"{API}/logout", headers=bearer(access), json={"refresh_token": refresh}), 401, "unauthorized")


def test_legacy_web_logout_unchanged(client, world, store):
	"""Portal flow: cookie login → POST /logout with cookies → both revoked, cookies cleared."""
	response = client.post("/login", json={"email": world.emails["operator_a"], "password": PASSWORD})
	assert response.status_code == 200
	access = client.get_cookie("access_token").value
	refresh = client.get_cookie("refresh_token").value

	response = client.post("/logout")  # no body, cookies only — exactly what auth-client.js sends
	assert response.status_code == 200
	assert response.get_json() == {"message": "Logged out"}
	cleared = response.headers.getlist("Set-Cookie")
	assert any(c.startswith("access_token=;") for c in cleared)
	assert any(c.startswith("refresh_token=;") for c in cleared)
	assert all("HttpOnly" in c for c in cleared)
	assert _session(store, access)["revoked"] is True
	assert _session(store, refresh)["revoked"] is True
	assert client.get("/verify", headers=bearer(access)).get_json() == {"error": "Token is revoked or user is inactive"}


def test_legacy_logout_with_bearer_only_unchanged(client, world, store):
	"""BackendWebFlask /sign-out calls Auth /logout with only `Authorization: Bearer <access>`."""
	access, refresh = tokens_for(client, world, "operator_a")
	response = client.post("/logout", headers=bearer(access))
	assert response.status_code == 200
	assert response.get_json() == {"message": "Logged out"}
	assert _session(store, access)["revoked"] is True


# --- /me and /permissions ---------------------------------------------------------------


def test_me_reads_current_postgresql_data(client, world, db_conn):
	access, _ = tokens_for(client, world, "operator_a")
	db_conn.execute(
		text("""UPDATE "user" SET name = 'Renamed', ui_preferences = '{"locale": "en", "theme": "light"}' WHERE id = :id"""),
		{"id": world.users["operator_a"]},
	)
	response = client.get(f"{API}/me", headers=bearer(access))
	assert response.status_code == 200
	body = response.get_json()
	assert list(body) == USER_FIELDS
	assert body["name"] == "Renamed"
	assert body["ui_preferences"] == {"locale": "en", "theme": "light"}
	raw = response.get_data(as_text=True)
	for forbidden in ("password", "pbkdf2", "jti", access):
		assert forbidden not in raw


def test_me_without_token_is_401(client, world):
	assert_envelope(client.get(f"{API}/me"), 401, "unauthorized")


def test_me_xml_matches_json(client, world):
	access, _ = tokens_for(client, world, "multi_a")
	as_json = client.get(f"{API}/me", headers=bearer(access)).get_json()
	root = ET.fromstring(client.get(f"{API}/me", headers={**bearer(access), "Accept": "application/xml"}).data)
	assert root.tag == "user"
	assert [c.tag for c in root] == USER_FIELDS
	assert [r.text for r in root.find("roles")] == as_json["roles"] == ["it_admin", "station_operator"]


def test_permissions_multi_role(client, world):
	access, _ = tokens_for(client, world, "multi_a")
	response = client.get(f"{API}/permissions", headers=bearer(access))
	assert response.status_code == 200
	assert response.get_json() == {
		"user_id": str(world.users["multi_a"]),
		"institution_id": str(world.institution_a),
		"roles": ["it_admin", "station_operator"],
	}


def test_permissions_do_not_trust_role_claim(client, world, mint):
	token = mint(world.users["operator_a"], world.institution_a, role="it_admin")
	assert client.get(f"{API}/permissions", headers=bearer(token)).get_json()["roles"] == ["station_operator"]


def test_permissions_no_cross_tenant_roles(client, world):
	access, _ = tokens_for(client, world, "foreign_a")
	body = client.get(f"{API}/permissions", headers=bearer(access)).get_json()
	assert body["roles"] == ["station_operator"]
	assert body["institution_id"] == str(world.institution_a)


def test_permissions_reflect_role_changes_without_new_login(client, world, db_conn):
	access, _ = tokens_for(client, world, "operator_a")
	db_conn.execute(text("DELETE FROM user_role WHERE user_id = :id"), {"id": world.users["operator_a"]})
	assert client.get(f"{API}/permissions", headers=bearer(access)).get_json()["roles"] == []


def test_permissions_xml(client, world):
	access, _ = tokens_for(client, world, "multi_a")
	root = ET.fromstring(client.get(f"{API}/permissions", headers={**bearer(access), "Accept": "application/xml"}).data)
	assert root.tag == "permissions"
	assert [c.tag for c in root] == ["user_id", "institution_id", "roles"]
	assert [r.text for r in root.find("roles")] == ["it_admin", "station_operator"]


# --- Correlation id / errors ------------------------------------------------------------


def test_correlation_id_is_preserved(client, world):
	response = login(client, world, "operator_a", headers={"X-Correlation-ID": "corr-auth-1"})
	assert response.headers["X-Correlation-ID"] == "corr-auth-1"


@pytest.mark.parametrize("incoming", [None, "", "bad value <x>", "x" * 65])
def test_correlation_id_generated_when_missing_or_invalid(client, world, incoming):
	headers = {"X-Correlation-ID": incoming} if incoming is not None else {}
	response = client.get(f"{API}/me", headers=headers)
	generated = response.headers["X-Correlation-ID"]
	assert generated != incoming and uuid.UUID(generated)
	assert response.get_json()["correlation_id"] == generated


def test_error_envelope_xml(client, world):
	response = client.get(f"{API}/me", headers={"Accept": "application/xml", "X-Correlation-ID": "corr-xml-2"})
	assert response.status_code == 401
	root = ET.fromstring(response.data)
	assert root.tag == "error_response"
	assert [c.tag for c in root] == ["error", "message", "correlation_id", "details"]
	assert root.findtext("correlation_id") == "corr-xml-2"


def test_unknown_api_route_uses_envelope(client, world):
	assert_envelope(client.get(f"{API}/register"), 404, "not_found")
	assert_envelope(client.get(f"{API}/login"), 405, "method_not_allowed")


def test_api_redis_down_is_503(client, world, monkeypatch):
	from token_store import RedisTokenStore

	monkeypatch.setattr(main, "token_store", RedisTokenStore("redis://127.0.0.1:1/0"))
	main.token_store.client.connection_pool.connection_kwargs.update(socket_connect_timeout=0.5, socket_timeout=0.5)
	body = assert_envelope(login(client, world, "operator_a"), 503, "dependency_unavailable")
	assert body["details"] == {"dependency": "redis"}


# --- Health -----------------------------------------------------------------------------


def test_health_endpoints_ok(client):
	expected = {
		"/health/live": ("live", {}),
		"/health/database": ("ok", {"database": "ok"}),
		"/health/redis": ("ok", {"redis": "ok"}),
		"/health/ready": ("ready", {"database": "ok", "redis": "ok"}),
	}
	for path, (status, checks) in expected.items():
		response = client.get(path, headers={"X-Correlation-ID": "corr-h"})
		assert response.status_code == 200, path
		assert response.get_json() == {"status": status, "checks": checks, "correlation_id": "corr-h"}


def test_health_ready_xml(client):
	root = ET.fromstring(client.get("/health/ready", headers={"Accept": "application/xml"}).data)
	assert root.tag == "health" and root.findtext("status") == "ready"


def test_health_redis_down(client, monkeypatch):
	from token_store import RedisTokenStore

	broken = RedisTokenStore("redis://127.0.0.1:1/0")
	broken.client.connection_pool.connection_kwargs.update(socket_connect_timeout=0.5, socket_timeout=0.5)
	monkeypatch.setattr(main, "token_store", broken)
	assert client.get("/health/redis").status_code == 503
	ready = client.get("/health/ready")
	assert ready.status_code == 503
	assert ready.get_json()["checks"] == {"database": "ok", "redis": "error"}
	assert client.get("/health/live").status_code == 200


def test_health_database_down(store, monkeypatch):
	from sqlalchemy import create_engine

	monkeypatch.setattr(main, "engine", create_engine("postgresql+psycopg://x:y@127.0.0.1:1/z", connect_args={"connect_timeout": 1}))
	client = main.app.test_client()
	assert client.get("/health/database").status_code == 503
	assert client.get("/health/ready").get_json()["checks"] == {"database": "error", "redis": "ok"}


def test_no_mongo_or_storage_health(client):
	assert client.get("/health/mongodb").status_code == 404
	assert client.get("/health/storage").status_code == 404


def test_legacy_health_unchanged(client):
	response = client.get("/health")
	assert response.status_code == 200
	assert response.get_json() == {"ok": True, "checks": {"postgres": True, "redis": True}}


# --- DS09 v0.1.5: X-Correlation-ID maximum 64 characters, never truncated ----------------------

CORR_64 = "a" * 30 + "-._:" + "7" * 30
CORR_65 = CORR_64 + "x"


class _Capture(logging.Handler):
	"""Request-log capture that applies the service's own correlation filter."""

	def __init__(self):
		super().__init__()
		self.records = []
		self.addFilter(CorrelationIdFilter())

	def emit(self, record):
		self.records.append(record)


@pytest.fixture
def request_log():
	target = logging.getLogger("auth.api")
	capture, previous = _Capture(), target.level
	target.addHandler(capture)
	target.setLevel(logging.INFO)
	yield capture.records
	target.removeHandler(capture)
	target.setLevel(previous)


def _envelope_correlation(response, accept):
	if accept.endswith("json"):
		return response.get_json()["correlation_id"]
	return ET.fromstring(response.data).findtext("correlation_id")


@pytest.mark.parametrize("accept", ["application/json", "application/xml"])
def test_64_char_correlation_id_is_preserved(client, world, request_log, accept):
	assert len(CORR_64) == 64
	response = client.get(f"{API}/me", headers={"X-Correlation-ID": CORR_64, "Accept": accept})
	assert response.status_code == 401 and response.headers["X-Correlation-ID"] == CORR_64
	assert _envelope_correlation(response, accept) == CORR_64
	assert [r.correlation_id for r in request_log] == [CORR_64]


@pytest.mark.parametrize("accept", ["application/json", "application/xml"])
def test_65_char_correlation_id_is_replaced_by_uuid(client, world, request_log, accept):
	response = client.get(f"{API}/me", headers={"X-Correlation-ID": CORR_65, "Accept": accept})
	generated = response.headers["X-Correlation-ID"]
	assert str(uuid.UUID(generated)) == generated
	assert _envelope_correlation(response, accept) == generated
	assert [r.correlation_id for r in request_log] == [generated]


def test_64_char_correlation_id_on_successful_login(client, world):
	response = login(client, world, "operator_a", headers={"X-Correlation-ID": CORR_64})
	assert response.status_code == 200 and response.headers["X-Correlation-ID"] == CORR_64


def test_correlation_pattern_matches_whole_header():
	from api_format import _CORRELATION_ID_PATTERN

	assert _CORRELATION_ID_PATTERN.fullmatch("corr-1\n") is None
	assert _CORRELATION_ID_PATTERN.fullmatch(CORR_64) and not _CORRELATION_ID_PATTERN.fullmatch(CORR_65)


def test_request_with_trailing_newline_gets_a_new_uuid(client, world):
	"""Real request: `^...$` with re.match would accept "abc\\n"; the service must use a full match."""
	response = client.get(f"{API}/me", environ_overrides={"HTTP_X_CORRELATION_ID": "abc\n"})
	generated = response.headers["X-Correlation-ID"]
	assert str(uuid.UUID(generated)) == generated
	assert response.get_json()["correlation_id"] == generated
