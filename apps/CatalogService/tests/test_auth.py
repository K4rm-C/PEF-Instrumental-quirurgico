"""JWT + Redis session + PostgreSQL identity validation."""

from __future__ import annotations

from datetime import timedelta

import pytest

from conftest import make_settings
from main import create_app

FAMILIES = "/api/v1/catalog/families"


def assert_unauthorized(response):
    assert response.status_code == 401
    body = response.get_json()
    assert body["error"] == "unauthorized"
    assert body["correlation_id"]
    return body


def test_missing_token_is_401(client, world):
    assert_unauthorized(client.get(FAMILIES))


@pytest.mark.parametrize("header", ["Bearer", "Bearer   ", "Basic abc", "Token abc", "abc"])
def test_malformed_authorization_header_is_401(client, world, header):
    assert_unauthorized(client.get(FAMILIES, headers={"Authorization": header}))


def test_garbage_token_is_401(client, world):
    assert_unauthorized(client.get(FAMILIES, headers={"Authorization": "Bearer not.a.jwt"}))


def test_wrong_signature_is_401(client, world, auth_header):
    headers = auth_header("operator_a", secret="another-secret-0123456789abcdef-xyz")
    assert_unauthorized(client.get(FAMILIES, headers=headers))


def test_expired_token_is_401(client, world, auth_header):
    body = assert_unauthorized(client.get(FAMILIES, headers=auth_header("operator_a", lifetime=timedelta(seconds=-5))))
    assert "expired" in body["message"].lower()


def test_refresh_token_is_rejected(client, world, auth_header):
    assert_unauthorized(client.get(FAMILIES, headers=auth_header("operator_a", token_type="refresh")))


def test_redis_token_type_mismatch_is_401(client, world, auth_header):
    headers = auth_header("operator_a", stored_token_type="refresh")
    assert_unauthorized(client.get(FAMILIES, headers=headers))


def test_token_absent_from_redis_is_401(client, world, auth_header):
    assert_unauthorized(client.get(FAMILIES, headers=auth_header("operator_a", store=False)))


def test_revoked_token_is_401(client, world, auth_header):
    assert_unauthorized(client.get(FAMILIES, headers=auth_header("operator_a", revoked=True)))


def test_redis_user_id_must_match_sub(client, world, auth_header):
    headers = auth_header("operator_a", stored_user_id=world.users["admin_a"])
    assert_unauthorized(client.get(FAMILIES, headers=headers))


def test_inactive_user_is_401(client, world, auth_header):
    assert_unauthorized(client.get(FAMILIES, headers=auth_header("inactive_a")))


def test_user_deactivated_after_login_is_401(client, world, auth_header, db_conn):
    from sqlalchemy import text

    headers = auth_header("operator_a")
    assert client.get(FAMILIES, headers=headers).status_code == 200
    db_conn.execute(text('UPDATE "user" SET active = FALSE WHERE id = :id'), {"id": world.users["operator_a"]})
    assert_unauthorized(client.get(FAMILIES, headers=headers))


def test_unknown_user_is_401(client, world, tokens):
    import uuid

    token = tokens.issue(uuid.uuid4(), world.institution_a)
    assert_unauthorized(client.get(FAMILIES, headers={"Authorization": f"Bearer {token}"}))


def test_institution_mismatch_with_session_is_401(client, world, tokens):
    token = tokens.issue(world.users["operator_a"], world.institution_b)
    assert_unauthorized(client.get(FAMILIES, headers={"Authorization": f"Bearer {token}"}))


def test_cookie_is_not_accepted_as_credential(client, world, tokens):
    token = tokens.issue(world.users["operator_a"], world.institution_a)
    client.set_cookie("access_token", token)
    assert_unauthorized(client.get(FAMILIES))


def test_valid_token_is_accepted(client, world, auth_header):
    assert client.get(FAMILIES, headers=auth_header("operator_a")).status_code == 200


def test_error_body_never_leaks_token(client, world, auth_header):
    headers = auth_header("operator_a", revoked=True)
    response = client.get(FAMILIES, headers=headers)
    raw_token = headers["Authorization"].split(" ", 1)[1]
    assert raw_token not in response.get_data(as_text=True)


def test_redis_unavailable_is_503(db_conn, world, tokens):
    from conftest import TransactionalDatabase
    import redis

    unreachable = redis.Redis(host="127.0.0.1", port=1, socket_connect_timeout=0.5, socket_timeout=0.5)
    app = create_app(make_settings(), database=TransactionalDatabase(db_conn), redis_client=unreachable)
    token = tokens.issue(world.users["operator_a"], world.institution_a)
    response = app.test_client().get(FAMILIES, headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 503
    body = response.get_json()
    assert body["error"] == "dependency_unavailable"
    assert body["details"] == {"dependency": "redis"}
