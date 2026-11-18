"""MS-07: machine clients (PEF-DS08) — client credentials, scopes, revocation, isolation (real PostgreSQL/Redis)."""

from __future__ import annotations

import base64
import json
import uuid

import jwt
import pytest
from sqlalchemy import text
from werkzeug.security import generate_password_hash

import main
from tests.test_api_v1 import login

SECRET = "s3cr3t-client-value-for-tests-only"


@pytest.fixture
def machine(db_conn, world):
    def add(key, institution, *, scopes=("sync.catalog.read", "fhir.encounter.read"), active=True):
        row_id = uuid.uuid4()
        db_conn.execute(text("""INSERT INTO integration_client (id, client_id, name, secret_hash, scopes, active, institution_id)
                                VALUES (:id, :cid, :n, :h, CAST(:s AS jsonb), :a, :i)"""),
                        {"id": row_id, "cid": f"{key}-{world.tag}", "n": key, "a": active, "i": institution,
                         "h": generate_password_hash(SECRET, method="pbkdf2:sha256:1000"), "s": json.dumps(list(scopes))})
        return row_id, f"{key}-{world.tag}"
    return add


def token(client, client_id, secret=SECRET, **extra):
    return client.post("/api/v1/auth/token", data={"grant_type": "client_credentials", "client_id": client_id,
                                                   "client_secret": secret, **extra})


def test_client_credentials_issue_a_scoped_client_token(client, world, machine, store, db_conn):
    row_id, client_id = machine("his", world.institution_a)
    response = token(client, client_id)
    assert response.status_code == 200 and response.headers["Cache-Control"] == "no-store"
    body = response.get_json()
    assert body["token_type"] == "Bearer" and body["expires_in"] == main.app.config["CLIENT_TOKEN_SECONDS"]
    assert body["scope"] == "fhir.encounter.read sync.catalog.read" and "refresh_token" not in body
    claims = jwt.decode(body["access_token"], main.app.config["JWT_SECRET_KEY"], algorithms=[main.app.config["JWT_ALGORITHM"]])
    assert claims["type"] == "client_access" and claims["sub"] == str(row_id) and claims["inst"] == str(world.institution_a)
    stored = json.loads(store.client.get(f"auth:jwt:{claims['jti']}"))
    assert stored["token_type"] == "client_access" and stored["client_id"] == str(row_id) and stored["user_id"] is None
    audit = db_conn.execute(text("SELECT actor_type, actor_client_id, action, outcome FROM access_audit "
                                 "WHERE actor_client_id = :c"), {"c": row_id}).all()
    assert audit == [("client", row_id, "CLIENT_TOKEN_ISSUED", "success")]
    assert db_conn.execute(text("SELECT last_used_at IS NOT NULL FROM integration_client WHERE id = :c"),
                           {"c": row_id}).scalar_one()
    store.client.delete(f"auth:jwt:{claims['jti']}", f"auth:client_tokens:{row_id}")


def test_basic_auth_json_and_scope_subset(client, world, machine, store):
    _, client_id = machine("basic", world.institution_a)
    basic = base64.b64encode(f"{client_id}:{SECRET}".encode()).decode()
    response = client.post("/api/v1/auth/token", data={"grant_type": "client_credentials", "scope": "sync.catalog.read"},
                           headers={"Authorization": f"Basic {basic}"})
    assert response.status_code == 200 and response.get_json()["scope"] == "sync.catalog.read"
    as_json = client.post("/api/v1/auth/token", json={"grant_type": "client_credentials", "client_id": client_id,
                                                      "client_secret": SECRET})
    assert as_json.status_code == 200
    extra = token(client, client_id, scope="fhir.patient.read")
    assert extra.status_code == 400 and extra.get_json()["error"] == "invalid_scope"


@pytest.mark.parametrize("case", ["wrong_secret", "unknown", "inactive", "institution_off"])
def test_invalid_clients_get_the_same_401(client, world, machine, case, db_conn):
    if case == "inactive":
        _, client_id = machine("off", world.institution_a, active=False)
    elif case == "institution_off":
        _, client_id = machine("offinst", world.institution_off)
    else:
        _, client_id = machine("x", world.institution_a)
    if case == "unknown":
        client_id = f"nobody-{world.tag}"
    response = token(client, client_id, "wrong" if case == "wrong_secret" else SECRET)
    assert response.status_code == 401 and response.get_json()["error"] == "invalid_client"


def test_grant_type_and_body_validation(client, world, machine):
    _, client_id = machine("v", world.institution_a)
    assert token(client, client_id, grant_type="password").get_json()["error"] == "unsupported_grant_type"
    assert client.post("/api/v1/auth/token", data={"grant_type": "client_credentials", "client_id": client_id,
                                                   "client_secret": SECRET, "username": "x"}).status_code == 400
    assert client.post("/api/v1/auth/token", data="x", content_type="text/plain").status_code == 400


def test_failures_are_throttled(client, world, machine, monkeypatch):
    _, client_id = machine("brute", world.institution_a)
    blocked = None
    for _ in range(12):
        blocked = token(client, client_id, "wrong")
        if blocked.status_code == 429:
            break
    assert blocked.status_code == 429
    assert token(client, client_id).status_code == 429          # even with the right secret while blocked


def test_a_client_token_is_not_a_user_token(client, world, machine, store):
    _, client_id = machine("nouser", world.institution_a)
    access = token(client, client_id).get_json()["access_token"]
    assert client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {access}"}).status_code == 401
    assert client.get("/api/v1/auth/users", headers={"Authorization": f"Bearer {access}"}).status_code == 401


def test_admin_revokes_every_token_of_a_client_of_its_institution(client, world, machine, store):
    row_id, client_id = machine("rev", world.institution_a)
    first, second = (jwt.decode(token(client, client_id).get_json()["access_token"], options={"verify_signature": False})
                     for _ in range(2))
    admin = login(client, world, "admin_a").get_json()["access_token"]
    foreign_admin_row, _ = machine("revb", world.institution_b)
    assert client.post(f"/api/v1/auth/clients/{foreign_admin_row}/revoke",
                       headers={"Authorization": f"Bearer {admin}"}).status_code == 404
    operator = login(client, world, "operator_a").get_json()["access_token"]
    assert client.post(f"/api/v1/auth/clients/{row_id}/revoke",
                       headers={"Authorization": f"Bearer {operator}"}).status_code == 403
    response = client.post(f"/api/v1/auth/clients/{row_id}/revoke", headers={"Authorization": f"Bearer {admin}"})
    assert response.status_code == 200 and response.get_json()["revoked"] == 2
    for claims in (first, second):
        assert json.loads(store.client.get(f"auth:jwt:{claims['jti']}"))["revoked"] is True
