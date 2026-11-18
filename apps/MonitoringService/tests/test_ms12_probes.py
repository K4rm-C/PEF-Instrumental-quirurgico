"""MS-12 (ms-monitoreo): IntegrationService and its delivery worker are monitored; PostgreSQL reports connection usage.
Real PostgreSQL (rolled back) and real connections identified by application_name."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine, text

from config import COMPONENTS
from services import probes


def test_the_integration_service_and_its_worker_are_components():
    assert COMPONENTS["integration"][1] == "http_health"
    assert COMPONENTS["integration-worker"][1] == "integration_worker"


def test_postgres_reports_connection_usage(pg_engine):
    result = probes.postgres("postgres", pg_engine, 2.0, 1500)
    assert result.status == "up"
    assert 1 <= result.details["connections"] <= result.details["max_connections"]


def _delivery(db_conn, *, status="pending", due_minutes_ago=0.0):
    institution, endpoint = uuid.uuid4(), uuid.uuid4()
    db_conn.execute(text("INSERT INTO institution (id, name) VALUES (:i, :n)"), {"i": institution, "n": f"Mon {institution.hex[:6]}"})
    db_conn.execute(text("INSERT INTO integration_endpoint (id, institution_id, name, base_url, resource_types) "
                         "VALUES (:e, :i, 'FHIR test', 'https://fhir.test', CAST('[\"Encounter\"]' AS jsonb))"),
                    {"e": endpoint, "i": institution})
    db_conn.execute(text("""INSERT INTO integration_delivery (endpoint_id, institution_id, resource_type, resource_id,
                            idempotency_key, request_hash, status, next_attempt_at)
                            VALUES (:e, :i, 'Encounter', :r, :k, :h, :s, :due)"""),
                    {"e": endpoint, "i": institution, "r": uuid.uuid4(), "k": uuid.uuid4().hex, "h": "a" * 64,
                     "s": status, "due": datetime.now(timezone.utc) - timedelta(minutes=due_minutes_ago)})


def test_integration_worker_connection_due_backlog_and_failures(pg_engine, database, db_conn):
    # Start from an empty queue inside the rolled-back transaction (other suites may leave rows).
    db_conn.execute(text("UPDATE integration_delivery SET status = 'sent' WHERE status = 'pending'"))
    app_name = f"pef-test-intworker-{uuid.uuid4().hex[:6]}"
    absent = probes.integration_worker("integration-worker", database.engine, 2.0, 1500, 120, app_name)
    assert (absent.status, absent.error_type) == ("down", "process_not_connected")
    worker = create_engine(pg_engine.url, connect_args={"application_name": app_name}).connect()
    worker.execute(text("SELECT 1"))
    db_conn.execute(text("SELECT pg_stat_clear_snapshot()"))
    try:
        _delivery(db_conn, due_minutes_ago=-5)                    # waiting for its next retry: not a backlog
        _delivery(db_conn, status="failed")
        alive = probes.integration_worker("integration-worker", database.engine, 2.0, 1500, 120, app_name)
        assert alive.status == "up" and alive.details["due_deliveries"] == 0
        assert alive.details["failed_last_24h"] >= 1
        _delivery(db_conn, due_minutes_ago=10)                    # due for 10 minutes: the worker is not keeping up
        stuck = probes.integration_worker("integration-worker", database.engine, 2.0, 1500, 120, app_name)
        assert (stuck.status, stuck.error_type) == ("degraded", "backlog_stalled")
        assert stuck.details["due_deliveries"] == 1 and stuck.details["oldest_due_seconds"] >= 590
        assert stuck.dependencies == {"database_connection": "ok", "delivery_backlog": "error"}
    finally:
        worker.close()
