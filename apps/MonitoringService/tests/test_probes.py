"""Classification of each probe against real HTTP servers, PostgreSQL and Redis (no mocks)."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
import redis
from sqlalchemy import create_engine, text

from services import probes

OK = {"status": "ready", "checks": {"database": "ok", "redis": "ok"}}


def test_ready_component_is_up_with_its_dependencies_and_real_latency(http_server):
    result = probes.http_health("catalog", http_server(200, OK), 1.0, 1500)
    assert result.status == "up" and result.http_status == 200 and result.error_type is None
    assert result.dependencies == {"database": "ok", "redis": "ok"} and 0 < result.latency_ms < 1000


@pytest.mark.parametrize("checks, expected", [
    ({"database": "ok", "redis": "error"}, "degraded"),       # answers, one dependency failed
    ({"database": "error", "redis": "error"}, "down"),        # answers that nothing works
])
def test_not_ready_component(http_server, checks, expected):
    result = probes.http_health("session", http_server(503, {"status": "not_ready", "checks": checks}), 1.0, 1500)
    assert (result.status, result.error_type, result.http_status) == (expected, "dependency_failed", 503)
    assert result.dependencies == checks


def test_unexpected_answers(http_server):
    assert probes.http_health("a", http_server(404, {"error": "not_found"}), 1.0, 1500).error_type == "unexpected_status"
    weird = probes.http_health("a", http_server(200, b"hello", "text/plain"), 1.0, 1500)
    assert (weird.status, weird.error_type) == ("degraded", "invalid_response")
    assert probes.http_health("a", http_server(500, b"boom", "text/plain"), 1.0, 1500).status == "down"


def test_slow_component_is_degraded(http_server):
    result = probes.http_health("audit", http_server(200, OK, delay=0.3), 2.0, threshold_ms=100)
    assert (result.status, result.error_type) == ("degraded", "slow_response") and result.latency_ms >= 300


def test_timeout_and_refused_are_down(http_server, closed_port):
    hung = probes.http_health("evidence", http_server(200, OK, delay=3), 0.5, 1500)
    assert (hung.status, hung.error_type) == ("down", "timeout") and hung.latency_ms < 2000
    refused = probes.http_health("evidence", closed_port, 1.0, 1500)
    # Linux refuses at once; Windows retries the SYN until the connect timeout.
    assert refused.status == "down" and refused.error_type in ("connection_refused", "connection_error", "connect_timeout")
    unresolvable = probes.http_health("evidence", "http://no-such-host.invalid:5005", 2.0, 1500)
    assert unresolvable.status == "down" and unresolvable.error_type in ("dns_error", "connection_error", "connect_timeout")


def test_vision_worker_needs_its_weights(http_server):
    up = probes.vision("vision", http_server(200, {"ok": True, "weights_present": True, "bound_sessions": 2}), 1.0, 1500)
    assert up.status == "up" and up.dependencies == {"model_weights": "ok"} and up.details == {"bound_sessions": 2}
    missing = probes.vision("vision", http_server(200, {"ok": True, "weights_present": False}), 1.0, 1500)
    assert (missing.status, missing.error_type) == ("degraded", "model_weights_missing")


def test_garage_admin_health(http_server):
    assert probes.garage("garage", http_server(200, b"Garage is fully operational", "text/plain"), 1.0, 1500).status == "up"
    degraded = probes.garage("garage", http_server(503, b"degraded", "text/plain"), 1.0, 1500)
    assert (degraded.status, degraded.error_type) == ("degraded", "dependency_failed")


def test_endpoints_never_keep_credentials_or_queries():
    assert probes.safe_endpoint("mongodb://user:secret@mongo:27017/?authSource=admin") == "mongodb://mongo:27017/"
    assert probes.safe_endpoint("http://u:p@auth:5001/health/ready?token=x#f") == "http://auth:5001/health/ready"


def test_postgres_and_redis_real(pg_engine, redis_client):
    pg = probes.postgres("postgres", pg_engine, 2.0, 1500)
    assert pg.status == "up" and pg.latency_ms > 0 and "@" not in pg.endpoint and pg_engine.url.password not in pg.endpoint
    assert probes.redis_ping("redis", redis_client, 2.0, 1500).status == "up"
    bad = probes.redis_ping("redis", redis.Redis(host="127.0.0.1", port=1, socket_connect_timeout=0.5), 1.0, 1500)
    assert bad.status == "down" and bad.error_type in ("connection_error", "timeout")
    broken = create_engine("postgresql+psycopg://x:y@127.0.0.1:1/none", connect_args={"connect_timeout": 1})
    assert probes.postgres("postgres", broken, 1.0, 1500).status == "down"


def test_notifications_worker_connection_and_backlog(pg_engine, database, db_conn):
    # Start from an empty backlog (inside the rolled-back transaction): a shared database may hold old
    # unprocessed outbox events from other suites while no worker runs, which the probe rightly reports.
    db_conn.execute(text("UPDATE notification_outbox SET processed_at = now() WHERE processed_at IS NULL"))
    app_name = f"pef-test-worker-{uuid.uuid4().hex[:6]}"
    absent = probes.notifications_worker("notifications-worker", database.engine, 2.0, 1500, 120, app_name)
    assert (absent.status, absent.error_type) == ("down", "process_not_connected")
    worker = create_engine(pg_engine.url, connect_args={"application_name": app_name}).connect()
    worker.execute(text("SELECT 1"))
    # pg_stat_activity is a snapshot per transaction; the probe gets a fresh one per connection in production.
    db_conn.execute(text("SELECT pg_stat_clear_snapshot()"))
    try:
        alive = probes.notifications_worker("notifications-worker", database.engine, 2.0, 1500, 120, app_name)
        assert alive.status == "up" and alive.dependencies["database_connection"] == "ok"
        db_conn.execute(text("INSERT INTO notification_outbox (source_table, source_id, event_code, occurred_at, enqueued_at) "
                             "VALUES ('access_audit', :id, 'session.scheduled', now(), :old)"),
                        {"id": uuid.uuid4(), "old": datetime.now(timezone.utc) - timedelta(minutes=10)})
        stuck = probes.notifications_worker("notifications-worker", database.engine, 2.0, 1500, 120, app_name)
        assert (stuck.status, stuck.error_type) == ("degraded", "backlog_stalled")
        assert stuck.details["pending_events"] == 1 and stuck.details["oldest_pending_seconds"] >= 590
    finally:
        worker.close()
