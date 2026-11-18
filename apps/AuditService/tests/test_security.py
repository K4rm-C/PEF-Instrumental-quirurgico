"""Authentication (JWT + Redis + PostgreSQL) and the read-only guarantee."""

from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from conftest import make_settings
from database import create_database

BASE = "/api/v1/audit"


def test_missing_invalid_expired_revoked_or_unregistered_tokens_are_401(client, world, tokens):
    user = world.users["admin_a"]
    cases = [
        {},
        {"Authorization": "Bearer not-a-jwt"},
        {"Authorization": f"Bearer {tokens.issue(user.id, user.institution_id, lifetime=timedelta(seconds=-5))}"},
        {"Authorization": f"Bearer {tokens.issue(user.id, user.institution_id, revoked=True)}"},
        {"Authorization": f"Bearer {tokens.issue(user.id, user.institution_id, store=False)}"},
        {"Authorization": f"Bearer {tokens.issue(user.id, user.institution_id, secret='another-secret-0123456789abcdef-x')}"},
        {"X-User-Id": str(user.id), "X-User-Role": "it_admin"},   # headers are never identity
    ]
    for headers in cases:
        response = client.get(f"{BASE}/events", headers=headers)
        assert response.status_code == 401 and response.get_json()["error"] == "unauthorized", headers


def test_deactivated_user_loses_access_immediately(client, world, as_user, db_conn):
    headers = as_user("admin_a")
    assert client.get(f"{BASE}/events", headers=headers).status_code == 200
    db_conn.execute(text('UPDATE "user" SET active = FALSE WHERE id = :id'), {"id": world.users["admin_a"].id})
    assert client.get(f"{BASE}/events", headers=headers).status_code == 401


def test_writes_are_rejected_inside_the_read_transactions(pg_engine):
    """Real engine (no test transaction): Database.read() is READ ONLY in PostgreSQL itself."""
    database = create_database(make_settings(enforce_read_only=True))
    try:
        with pytest.raises(DBAPIError) as raised:
            with database.read() as connection:
                connection.execute(text("UPDATE access_audit SET outcome = outcome WHERE false"))
        assert "read-only" in str(raised.value).lower()
        with database.read() as connection:
            assert connection.execute(text("SELECT count(*) FROM access_audit")).scalar_one() >= 0
    finally:
        database.engine.dispose()


def test_no_write_endpoint_exists(client, world, as_user):
    for method in ("post", "put", "patch", "delete"):
        response = getattr(client, method)(f"{BASE}/events", headers=as_user("admin_a"), json={"action": "LOGIN"})
        assert response.status_code == 405
    assert client.delete(f"{BASE}/events/{world.events['login']}", headers=as_user("admin_a")).status_code == 405
