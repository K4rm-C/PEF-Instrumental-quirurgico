"""Health checks (PostgreSQL + Redis only; no Mongo/storage/vision)."""

from __future__ import annotations

import redis

from conftest import TransactionalDatabase, make_settings
from main import create_app


def _broken_redis():
    return redis.Redis(host="127.0.0.1", port=1, socket_connect_timeout=0.5, socket_timeout=0.5)


def _broken_db_app(redis_client):
    return create_app(make_settings(database_url="postgresql+psycopg://x:y@127.0.0.1:1/z"), redis_client=redis_client)


def test_all_health_ok(client):
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


def test_live_without_dependencies():
    app = create_app(make_settings(database_url="postgresql+psycopg://x:y@127.0.0.1:1/z"), redis_client=_broken_redis())
    assert app.test_client().get("/health/live").status_code == 200


def test_database_down(redis_client):
    client = _broken_db_app(redis_client).test_client()
    assert client.get("/health/database").status_code == 503
    ready = client.get("/health/ready")
    assert ready.status_code == 503
    assert ready.get_json()["checks"] == {"database": "error", "redis": "ok"}


def test_redis_down(db_conn):
    client = create_app(make_settings(), database=TransactionalDatabase(db_conn), redis_client=_broken_redis()).test_client()
    assert client.get("/health/redis").status_code == 503
    ready = client.get("/health/ready")
    assert ready.status_code == 503 and ready.get_json()["status"] == "not_ready"


def test_no_extra_health_endpoints(client):
    for path in ("/health/mongodb", "/health/storage", "/health/vision"):
        assert client.get(path).status_code == 404
