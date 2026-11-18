"""Health endpoints: live, database, redis, storage, ready (every relevant combination)."""

from __future__ import annotations

import itertools
import xml.etree.ElementTree as ET

import pytest
import redis as redis_lib
from sqlalchemy.exc import OperationalError

from conftest import TEST_JWT_SECRET, TEST_S3_SECRET, FakeStorage, TransactionalDatabase, make_settings
from main import create_app

FORBIDDEN_TEXT = (TEST_JWT_SECRET, TEST_S3_SECRET, "pytest-access-key", "storage.invalid", "password", "Traceback",
                  "SELECT", "botocore", "postgresql", "redis://")


class BrokenDatabase:
    def ping(self):
        raise OperationalError("SELECT 1", {}, Exception("could not connect to server: password authentication failed"))


class BrokenRedis:
    def ping(self):
        raise redis_lib.ConnectionError("Error 111 connecting to redis://:secret@redis:6379")


def _app(db_conn, redis_client, *, database_ok=True, redis_ok=True, storage_failure=None):
    return create_app(
        make_settings(),
        database=TransactionalDatabase(db_conn) if database_ok else BrokenDatabase(),
        redis_client=redis_client if redis_ok else BrokenRedis(),
        storage=FakeStorage(storage_failure),
    )


def _assert_clean(response):
    text = response.get_data(as_text=True)
    for fragment in FORBIDDEN_TEXT:
        assert fragment not in text, fragment


def test_live_has_no_dependencies(db_conn, redis_client):
    storage = FakeStorage("unreachable")
    app = create_app(make_settings(), database=BrokenDatabase(), redis_client=BrokenRedis(), storage=storage)
    response = app.test_client().get("/health/live")
    assert response.status_code == 200
    assert response.get_json() == {"service": "evidence", "status": "live", "checks": {},
                                   "correlation_id": response.headers["X-Correlation-ID"]}
    assert storage.calls == []


@pytest.mark.parametrize("path, key", [("/health/database", "database"), ("/health/redis", "redis"),
                                       ("/health/storage", "storage")])
def test_single_dependency_ok(db_conn, redis_client, path, key):
    response = _app(db_conn, redis_client).test_client().get(path)
    assert response.status_code == 200
    assert response.get_json()["status"] == "ok" and response.get_json()["checks"] == {key: "ok"}


@pytest.mark.parametrize("path, key, kwargs", [
    ("/health/database", "database", {"database_ok": False}),
    ("/health/redis", "redis", {"redis_ok": False}),
    ("/health/storage", "storage", {"storage_failure": "unreachable"}),
    ("/health/storage", "storage", {"storage_failure": "access_denied"}),
    ("/health/storage", "storage", {"storage_failure": "bucket_missing"}),
    ("/health/storage", "storage", {"storage_failure": "error"}),
])
def test_single_dependency_failure_is_503_without_details(db_conn, redis_client, path, key, kwargs):
    response = _app(db_conn, redis_client, **kwargs).test_client().get(path)
    assert response.status_code == 503
    assert response.get_json()["status"] == "unavailable" and response.get_json()["checks"] == {key: "error"}
    _assert_clean(response)


@pytest.mark.parametrize("database_ok, redis_ok, storage_ok", list(itertools.product([True, False], repeat=3)))
def test_ready_requires_all_three(db_conn, redis_client, database_ok, redis_ok, storage_ok):
    app = _app(db_conn, redis_client, database_ok=database_ok, redis_ok=redis_ok,
               storage_failure=None if storage_ok else "bucket_missing")
    response = app.test_client().get("/health/ready")
    all_ok = database_ok and redis_ok and storage_ok
    assert response.status_code == (200 if all_ok else 503)
    body = response.get_json()
    assert body["status"] == ("ready" if all_ok else "not_ready")
    assert body["checks"] == {"database": "ok" if database_ok else "error", "redis": "ok" if redis_ok else "error",
                              "storage": "ok" if storage_ok else "error"}
    _assert_clean(response)


def test_health_is_public_and_xml(db_conn, redis_client):
    response = _app(db_conn, redis_client).test_client().get("/health/ready", headers={"Accept": "application/xml"})
    root = ET.fromstring(response.data)
    assert response.status_code == 200 and root.tag == "health"
    assert [c.tag for c in root] == ["service", "status", "checks", "correlation_id"]
    assert root.find("checks").findtext("storage") == "ok"


def test_storage_health_only_checks_the_bucket(db_conn, redis_client):
    storage = FakeStorage()
    app = create_app(make_settings(), database=TransactionalDatabase(db_conn), redis_client=redis_client, storage=storage)
    client = app.test_client()
    client.get("/health/storage")
    client.get("/health/ready")
    assert storage.calls == ["check_bucket", "check_bucket"]
