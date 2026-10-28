"""JWT + Redis session + PostgreSQL identity + Session role gate."""

from __future__ import annotations

from datetime import timedelta

import jwt
import pytest
from sqlalchemy import text

from conftest import TransactionalDatabase, make_settings
from main import create_app

LIST = "/api/v1/sessions"

# HS384/HS512 tokens are forged on purpose with the HS256 test secret.
pytestmark = pytest.mark.filterwarnings("ignore:The HMAC key is")


def bearer(token):
    return {"Authorization": f"Bearer {token}"}


def assert_error(response, status, error):
    assert response.status_code == status, response.get_data(as_text=True)
    body = response.get_json()
    assert list(body) == ["error", "message", "correlation_id", "details"]
    assert body["error"] == error
    assert body["correlation_id"] == response.headers["X-Correlation-ID"]
    return body


def test_missing_token_is_401(client, world):
    assert_error(client.get(LIST), 401, "unauthorized")


@pytest.mark.parametrize("header", ["Bearer", "Bearer   ", "Basic abc", "abc", "Bearer not.a.jwt"])
def test_malformed_token_is_401(client, world, header):
    assert_error(client.get(LIST, headers={"Authorization": header}), 401, "unauthorized")


def _issue(tokens, world, key="spd_a", **kwargs):
    user = world.users[key]
    return tokens.issue(user.id, user.institution_id, **kwargs)


def test_expired_token_is_401(client, world, tokens):
    body = assert_error(client.get(LIST, headers=bearer(_issue(tokens, world, lifetime=timedelta(seconds=-5)))),
                        401, "unauthorized")
    assert "expired" in body["message"].lower()


def test_revoked_token_is_401(client, world, tokens):
    assert_error(client.get(LIST, headers=bearer(_issue(tokens, world, revoked=True))), 401, "unauthorized")


def test_token_absent_from_redis_is_401(client, world, tokens):
    assert_error(client.get(LIST, headers=bearer(_issue(tokens, world, store=False))), 401, "unauthorized")


def test_refresh_token_type_is_401(client, world, tokens):
    assert_error(client.get(LIST, headers=bearer(_issue(tokens, world, token_type="refresh"))), 401, "unauthorized")


def test_redis_token_type_mismatch_is_401(client, world, tokens):
    token = _issue(tokens, world, stored_token_type="refresh")
    assert_error(client.get(LIST, headers=bearer(token)), 401, "unauthorized")


def test_redis_user_mismatch_is_401(client, world, tokens):
    token = _issue(tokens, world, stored_user_id=world.users["op_a"].id)
    assert_error(client.get(LIST, headers=bearer(token)), 401, "unauthorized")


def test_redis_institution_mismatch_is_401(client, world, tokens):
    token = tokens.issue(world.users["spd_a"].id, world.institutions["B"])
    assert_error(client.get(LIST, headers=bearer(token)), 401, "unauthorized")


def test_wrong_secret_is_401(client, world, tokens):
    token = _issue(tokens, world, secret="another-secret-0123456789abcdef-0123456789")
    assert_error(client.get(LIST, headers=bearer(token)), 401, "unauthorized")


def test_configured_algorithm_hs256_is_accepted(client, world, tokens):
    assert client.get(LIST, headers=bearer(_issue(tokens, world, algorithm="HS256"))).status_code == 200


@pytest.mark.parametrize("algorithm", ["HS384", "HS512", "none"])
def test_other_algorithms_are_rejected(client, world, tokens, algorithm):
    token = _issue(tokens, world, algorithm=algorithm)
    assert jwt.get_unverified_header(token)["alg"] == algorithm
    assert_error(client.get(LIST, headers=bearer(token)), 401, "unauthorized")


def test_inactive_user_is_401(client, world, tokens):
    assert_error(client.get(LIST, headers=bearer(_issue(tokens, world, "inactive_a"))), 401, "unauthorized")


def test_inactive_institution_is_401(client, world, tokens):
    assert_error(client.get(LIST, headers=bearer(_issue(tokens, world, "off_user"))), 401, "unauthorized")


def test_user_deactivated_after_token_issue_is_401(client, world, tokens, db_conn):
    headers = bearer(_issue(tokens, world))
    assert client.get(LIST, headers=headers).status_code == 200
    db_conn.execute(text('UPDATE "user" SET active = FALSE WHERE id = :id'), {"id": world.users["spd_a"].id})
    assert_error(client.get(LIST, headers=headers), 401, "unauthorized")


@pytest.mark.parametrize("user_key", ["admin_a", "norole_a", "foreign_role_a"])
def test_users_without_session_role_get_403(client, world, as_user, user_key):
    session_id = world.sessions["a1"].id
    for path in (LIST, f"{LIST}/{session_id}", f"{LIST}/{session_id}/expected-inventory",
                 f"{LIST}/{session_id}/counts", f"{LIST}/{session_id}/discrepancies"):
        assert_error(client.get(path, headers=as_user(user_key)), 403, "forbidden")


def test_admin_gets_403_even_for_unknown_session(client, world, as_user):
    import uuid

    assert_error(client.get(f"{LIST}/{uuid.uuid4()}", headers=as_user("admin_a")), 403, "forbidden")


def test_role_claim_alone_does_not_grant_access(client, world, tokens):
    token = tokens.issue(world.users["admin_a"].id, world.institutions["A"], role="spd_supervisor")
    assert_error(client.get(LIST, headers=bearer(token)), 403, "forbidden")


def test_operator_plus_admin_keeps_operator_scope(client, world, as_user):
    # Has station_operator, so access is granted; no sessions are assigned to this user.
    response = client.get(LIST, headers=as_user("op_admin_a"))
    assert response.status_code == 200 and response.get_json() == []


def test_cookie_is_not_accepted(client, world, tokens):
    client.set_cookie("access_token", _issue(tokens, world))
    assert_error(client.get(LIST), 401, "unauthorized")


def test_redis_unavailable_is_503(db_conn, world, tokens):
    import redis

    broken = redis.Redis(host="127.0.0.1", port=1, socket_connect_timeout=0.5, socket_timeout=0.5)
    app = create_app(make_settings(), database=TransactionalDatabase(db_conn), redis_client=broken)
    response = app.test_client().get(LIST, headers=bearer(_issue(tokens, world)))
    body = assert_error(response, 503, "dependency_unavailable")
    assert body["details"] == {"dependency": "redis"}


def test_database_unavailable_is_503(world, tokens, redis_client):
    app = create_app(make_settings(database_url="postgresql+psycopg://nobody:nothing@127.0.0.1:1/none"),
                     redis_client=redis_client)
    response = app.test_client().get(LIST, headers=bearer(_issue(tokens, world)))
    body = assert_error(response, 503, "dependency_unavailable")
    assert body["details"] == {"dependency": "database"}
    assert "nothing" not in response.get_data(as_text=True)
