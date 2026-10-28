"""Sign-in audit (PEF-DS07 access_audit, read by AuditService): LOGIN, LOGIN_FAILED, LOGOUT."""

from __future__ import annotations

from sqlalchemy import text

from conftest import PASSWORD

API = "/api/v1/auth"


def events(db_conn, user_id):
	return [tuple(row) for row in db_conn.execute(text(
		"SELECT action, outcome, resource_type, institution_id::text, host(ip) FROM access_audit "
		"WHERE actor_user_id = :u AND action IN ('LOGIN', 'LOGIN_FAILED', 'LOGOUT') ORDER BY occurred_at, action"),
		{"u": str(user_id)})]


def test_portal_login_and_logout_are_audited_once(client, world, db_conn):
	user = world.users["operator_a"]
	assert client.post("/login", json={"email": world.emails["operator_a"], "password": PASSWORD},
					   headers={"X-Correlation-ID": "corr-login-1"}).status_code == 200
	assert client.post("/logout").status_code == 200
	rows = events(db_conn, user)
	assert sorted((r[0], r[1], r[2]) for r in rows) == [("LOGIN", "success", "user"), ("LOGOUT", "success", "user")]
	assert rows[0][3] == str(world.institution_a) and rows[0][4] == "127.0.0.1"
	corr = db_conn.execute(text("SELECT correlation_id FROM access_audit WHERE actor_user_id = :u AND action = 'LOGIN'"),
						   {"u": str(user)}).scalar_one()
	assert corr == "corr-login-1"


def test_api_login_logout_and_failed_attempt(client, world, db_conn):
	user = world.users["supervisor_a"]
	assert client.post(f"{API}/login", json={"email": world.emails["supervisor_a"], "password": "wrong-one"}).status_code == 401
	pair = client.post(f"{API}/login", json={"email": world.emails["supervisor_a"], "password": PASSWORD}).get_json()
	response = client.post(f"{API}/logout", json={"refresh_token": pair["refresh_token"]},
						   headers={"Authorization": f"Bearer {pair['access_token']}"})
	assert response.status_code == 204
	# One test transaction: now() is the same instant for every row, so compare without order.
	assert sorted((r[0], r[1]) for r in events(db_conn, user)) == [("LOGIN", "success"), ("LOGIN_FAILED", "denied"),
																	("LOGOUT", "success")]


def test_unknown_account_and_spoofed_forwarded_for_are_not_trusted(client, world, db_conn):
	before = db_conn.execute(text("SELECT count(*) FROM access_audit WHERE action = 'LOGIN_FAILED'")).scalar_one()
	assert client.post(f"{API}/login", json={"email": "nobody@auth-test.invalid", "password": "x"}).status_code == 401
	assert db_conn.execute(text("SELECT count(*) FROM access_audit WHERE action = 'LOGIN_FAILED'")).scalar_one() == before
	client.post("/login", json={"email": world.emails["operator_b"], "password": PASSWORD},
				headers={"X-Forwarded-For": "203.0.113.9"})
	ip = db_conn.execute(text("SELECT host(ip) FROM access_audit WHERE actor_user_id = :u AND action = 'LOGIN'"),
						 {"u": str(world.users["operator_b"])}).scalar_one()
	assert ip == "127.0.0.1"


def test_inactive_user_failed_login_is_audited_without_a_session(client, world, db_conn):
	assert client.post(f"{API}/login", json={"email": world.emails["inactive_a"], "password": PASSWORD}).status_code == 401
	assert [(r[0], r[1]) for r in events(db_conn, world.users["inactive_a"])] == [("LOGIN_FAILED", "denied")]
