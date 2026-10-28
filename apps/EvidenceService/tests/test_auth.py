"""JWT + Redis session + PostgreSQL identity + effective roles (through a test-only route)."""

from __future__ import annotations

from datetime import timedelta

import jwt
import pytest

WHOAMI = "/__test__/whoami"
CLINICAL = "/__test__/clinical"

# HS384/HS512 tokens are forged on purpose with the HS256 test secret.
pytestmark = pytest.mark.filterwarnings("ignore:The HMAC key is")


def bearer(token):
    return {"Authorization": f"Bearer {token}"}


def assert_error(response, status, error):
    assert response.status_code == status, response.get_data(as_text=True)
    body = response.get_json()
    assert list(body) == ["error", "message", "correlation_id", "details"]
    assert body["error"] == error and body["correlation_id"] == response.headers["X-Correlation-ID"]
    return body


def _issue(tokens, world, key="spd_a", **kwargs):
    user = world.users[key]
    return tokens.issue(user.id, user.institution_id, **kwargs)


def test_valid_token(client, world, tokens):
    response = client.get(WHOAMI, headers=bearer(_issue(tokens, world)))
    assert response.status_code == 200
    assert response.get_json() == {"user_id": str(world.users["spd_a"].id),
                                   "institution_id": str(world.institutions["A"]), "roles": ["spd_supervisor"]}


def test_missing_token(client, world):
    assert_error(client.get(WHOAMI), 401, "unauthorized")


@pytest.mark.parametrize("header", ["Bearer", "Bearer   ", "Basic abc", "abc", "Bearer not.a.jwt"])
def test_malformed_token(client, world, header):
    assert_error(client.get(WHOAMI, headers={"Authorization": header}), 401, "unauthorized")


def test_expired_token(client, world, tokens):
    body = assert_error(client.get(WHOAMI, headers=bearer(_issue(tokens, world, lifetime=timedelta(seconds=-5)))),
                        401, "unauthorized")
    assert "expired" in body["message"].lower()


def test_refresh_token_type_is_rejected(client, world, tokens):
    assert_error(client.get(WHOAMI, headers=bearer(_issue(tokens, world, token_type="refresh"))), 401, "unauthorized")


def test_stored_session_of_another_type_is_rejected(client, world, tokens):
    token = _issue(tokens, world, stored_token_type="refresh")
    assert_error(client.get(WHOAMI, headers=bearer(token)), 401, "unauthorized")


@pytest.mark.parametrize("claim", ["jti", "sub", "type", "exp"])
def test_missing_required_claim(client, world, tokens, claim):
    assert_error(client.get(WHOAMI, headers=bearer(_issue(tokens, world, drop_claims=(claim,), store=claim != "jti"))),
                 401, "unauthorized")


def test_absent_redis_session(client, world, tokens):
    assert_error(client.get(WHOAMI, headers=bearer(_issue(tokens, world, store=False))), 401, "unauthorized")


def test_revoked_session(client, world, tokens):
    assert_error(client.get(WHOAMI, headers=bearer(_issue(tokens, world, revoked=True))), 401, "unauthorized")


def test_redis_session_of_another_user(client, world, tokens):
    token = _issue(tokens, world, stored_user_id=world.users["op_a"].id)
    assert_error(client.get(WHOAMI, headers=bearer(token)), 401, "unauthorized")


def test_inactive_user(client, world, tokens):
    assert_error(client.get(WHOAMI, headers=bearer(_issue(tokens, world, "inactive_a"))), 401, "unauthorized")


def test_inactive_institution(client, world, tokens):
    assert_error(client.get(WHOAMI, headers=bearer(_issue(tokens, world, "off_user"))), 401, "unauthorized")


def test_institution_mismatch_with_redis(client, world, tokens):
    user = world.users["spd_a"]
    token = tokens.issue(user.id, world.institutions["B"])
    assert_error(client.get(WHOAMI, headers=bearer(token)), 401, "unauthorized")


@pytest.mark.parametrize("algorithm", ["HS384", "HS512"])
def test_other_hs_algorithm_is_rejected(client, world, tokens, algorithm):
    assert_error(client.get(WHOAMI, headers=bearer(_issue(tokens, world, algorithm=algorithm))), 401, "unauthorized")


def test_alg_none_is_rejected(client, world, tokens):
    assert_error(client.get(WHOAMI, headers=bearer(_issue(tokens, world, algorithm="none"))), 401, "unauthorized")


def test_wrong_secret_is_rejected(client, world, tokens):
    token = _issue(tokens, world, secret="another-secret-0123456789abcdef-xyz-0000")
    assert_error(client.get(WHOAMI, headers=bearer(token)), 401, "unauthorized")


def test_roles_come_from_database_not_from_the_claim(client, world, tokens):
    token = _issue(tokens, world, "op_a", role="spd_supervisor")
    assert client.get(WHOAMI, headers=bearer(token)).get_json()["roles"] == ["station_operator"]


def test_role_of_another_institution_is_not_effective(client, world, tokens):
    token = _issue(tokens, world, "foreign_role_a")
    assert client.get(WHOAMI, headers=bearer(token)).get_json()["roles"] == []
    assert_error(client.get(CLINICAL, headers=bearer(_issue(tokens, world, "foreign_role_a"))), 403, "forbidden")


def test_multi_role(client, world, tokens):
    token = _issue(tokens, world, "multi_a")
    assert client.get(WHOAMI, headers=bearer(token)).get_json()["roles"] == ["spd_supervisor", "station_operator"]


@pytest.mark.parametrize("key, status", [("op_a", 200), ("spd_a", 200), ("multi_a", 200), ("admin_a", 403)])
def test_clinical_role_gate(client, world, tokens, key, status):
    assert client.get(CLINICAL, headers=bearer(_issue(tokens, world, key))).status_code == status


def test_tenant_comes_from_postgresql(client, world, tokens):
    token = _issue(tokens, world, "op_b")
    assert client.get(WHOAMI, headers=bearer(token)).get_json()["institution_id"] == str(world.institutions["B"])


def test_cookies_are_ignored(client, world, tokens):
    client.set_cookie("access_token", _issue(tokens, world))
    assert_error(client.get(WHOAMI), 401, "unauthorized")


def test_redis_outage_is_503(app, world, tokens, monkeypatch):
    import redis as redis_lib

    token = _issue(tokens, world)

    def broken(*args, **kwargs):
        raise redis_lib.ConnectionError("Error connecting to redis://:secret@host")
    monkeypatch.setattr(app.extensions["evidence_service"]["redis"], "get", broken)
    response = app.test_client().get(WHOAMI, headers=bearer(token))
    body = assert_error(response, 503, "dependency_unavailable")
    assert body["details"] == {"dependency": "redis"} and "secret" not in response.get_data(as_text=True)


def test_jwt_library_rejects_tokens_signed_with_none_even_if_requested():
    """Guard for the decode call itself: algorithms=[configured] only."""
    token = jwt.encode({"sub": "x", "jti": "y", "type": "access", "exp": 9999999999}, None, algorithm="none")
    with pytest.raises(jwt.InvalidTokenError):
        jwt.decode(token, "secret", algorithms=["HS256"])
