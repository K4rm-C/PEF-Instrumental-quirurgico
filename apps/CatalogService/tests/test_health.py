"""Health checks: /health/live, /health/database, /health/redis, /health/ready."""

from __future__ import annotations

import xml.etree.ElementTree as ET

import redis

from conftest import TransactionalDatabase, make_settings
from main import create_app


def _broken_redis():
    return redis.Redis(host="127.0.0.1", port=1, socket_connect_timeout=0.5, socket_timeout=0.5)


def _app_with_broken_database(redis_client):
    return create_app(
        make_settings(database_url="postgresql+psycopg://nobody:nothing@127.0.0.1:1/none"),
        redis_client=redis_client,
    )


def test_live(client):
    response = client.get("/health/live", headers={"X-Correlation-ID": "corr-health-1"})
    assert response.status_code == 200
    assert response.get_json() == {"status": "live", "checks": {}, "correlation_id": "corr-health-1"}


def test_live_does_not_touch_dependencies(redis_client):
    app = _app_with_broken_database(_broken_redis())
    assert app.test_client().get("/health/live").status_code == 200


def test_database_ok(client):
    response = client.get("/health/database")
    assert response.status_code == 200
    assert response.get_json()["checks"] == {"database": "ok"}


def test_redis_ok(client):
    response = client.get("/health/redis")
    assert response.status_code == 200
    assert response.get_json()["checks"] == {"redis": "ok"}


def test_ready_ok(client):
    response = client.get("/health/ready", headers={"X-Correlation-ID": "corr-health-1"})
    assert response.status_code == 200
    assert response.get_json() == {
        "status": "ready",
        "checks": {"database": "ok", "redis": "ok"},
        "correlation_id": "corr-health-1",
    }


def test_ready_xml(client):
    response = client.get("/health/ready", headers={"Accept": "application/xml"})
    root = ET.fromstring(response.data)
    assert root.tag == "health"
    assert root.findtext("status") == "ready"
    assert root.find("checks").findtext("database") == "ok"


def test_database_down(redis_client):
    client = _app_with_broken_database(redis_client).test_client()
    database = client.get("/health/database")
    assert database.status_code == 503
    assert database.get_json()["checks"] == {"database": "error"}
    ready = client.get("/health/ready")
    assert ready.status_code == 503
    assert ready.get_json()["status"] == "not_ready"
    assert ready.get_json()["checks"] == {"database": "error", "redis": "ok"}


def test_redis_down(db_conn):
    client = create_app(make_settings(), database=TransactionalDatabase(db_conn), redis_client=_broken_redis()).test_client()
    assert client.get("/health/redis").status_code == 503
    ready = client.get("/health/ready")
    assert ready.status_code == 503
    assert ready.get_json()["checks"] == {"database": "ok", "redis": "error"}


def test_health_does_not_require_auth(client):
    for path in ("/health/live", "/health/database", "/health/redis", "/health/ready"):
        assert client.get(path).status_code == 200


def test_no_mongo_or_storage_health(client):
    assert client.get("/health/mongodb").status_code == 404
    assert client.get("/health/storage").status_code == 404
