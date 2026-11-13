"""Collector cycle: every component checked in parallel, stored once, bounded retention, no secrets."""

from __future__ import annotations

import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import text

from config import COMPONENTS
from services.collector import Collector

from conftest import make_settings

OK = {"status": "ready", "checks": {"database": "ok", "redis": "ok"}}


def _collector(database, redis_client, **targets):
    settings = make_settings(targets=targets, check_timeout_seconds=1.0)
    # Stored rows go through the test's transaction; the parallel PostgreSQL probes use the real engine.
    return Collector(settings, database, redis_client, probe_engine=database.real_engine)


def test_migration_is_idempotent_and_shipped_with_the_image(pg_engine):
    from database import apply_migrations
    apply_migrations(pg_engine)
    apply_migrations(pg_engine)
    service_dir = Path(__file__).resolve().parents[1]
    assert (service_dir / "migrations" / "007_monitoring.sql").read_bytes() == \
        (service_dir.parents[1] / "data" / "migrations" / "007_monitoring.sql").read_bytes()


def test_one_cycle_checks_and_stores_every_component(database, redis_client, db_conn, http_server, closed_port):
    collector = _collector(database, redis_client, catalog=http_server(200, OK), session=closed_port)
    try:
        run_id, results = collector.run_once()
    finally:
        collector.close()
    assert run_id is not None and {r.component for r in results} == set(COMPONENTS)
    rows = dict(db_conn.execute(text("SELECT component, status FROM monitoring_check WHERE run_id = :r"), {"r": run_id}).all())
    assert len(rows) == len(COMPONENTS)
    assert rows["catalog"] == "up" and rows["session"] == "down" and rows["postgres"] == "up" and rows["redis"] == "up"
    assert rows["auth"] == "unknown" and rows["mongo"] == "unknown"           # not configured: never "available"
    run = db_conn.execute(text("SELECT components, failures FROM monitoring_run WHERE id = :r"), {"r": run_id}).one()
    assert run[0] == len(COMPONENTS) and run[1] >= 1


def test_a_hanging_component_does_not_delay_the_others(database, redis_client, http_server):
    hanging = http_server(200, OK, delay=5)
    targets = {name: http_server(200, OK) for name in ("auth", "catalog", "session", "audit")}
    collector = _collector(database, redis_client, evidence=hanging, **targets)
    started = time.monotonic()
    try:
        _, results = collector.run_once()
    finally:
        collector.close()
    elapsed = time.monotonic() - started
    by_name = {r.component: r for r in results}
    assert by_name["evidence"].status == "down" and by_name["evidence"].error_type == "timeout"
    assert all(by_name[name].status == "up" for name in targets)
    assert elapsed < 3.5, elapsed          # about one timeout, not one per component


def test_history_never_stores_credentials(database, redis_client, db_conn, http_server, pg_engine):
    collector = _collector(database, redis_client, catalog=http_server(200, OK))
    try:
        run_id, _ = collector.run_once()
    finally:
        collector.close()
    stored = " ".join(str(v) for row in db_conn.execute(text(
        "SELECT endpoint, dependencies::text, details::text, error_type FROM monitoring_check WHERE run_id = :r"),
        {"r": run_id}).all() for v in row)
    assert pg_engine.url.password not in stored and "Bearer" not in stored and "@" not in stored


def test_retention_deletes_only_old_runs_and_their_checks(database, redis_client, db_conn, history):
    old = history("catalog", "up", 10, minutes_ago=60 * 24 * 8)
    recent = history("catalog", "up", 10, minutes_ago=5)
    collector = _collector(database, redis_client)
    try:
        assert collector.purge() == 1
    finally:
        collector.close()
    ids = {row[0] for row in db_conn.execute(text("SELECT id FROM monitoring_check")).all()}
    assert old not in ids and recent in ids


def test_a_database_outage_is_logged_not_raised(database, redis_client, http_server, monkeypatch):
    collector = _collector(database, redis_client, catalog=http_server(200, OK))
    monkeypatch.setattr(collector, "store", lambda results: (_ for _ in ()).throw(RuntimeError("database down")))
    try:
        run_id, results = collector.run_once()
    finally:
        collector.close()
    assert run_id is None and len(results) == len(COMPONENTS)


# --- MS-11: availability transitions -> service_event (017) -> notification outbox ---------------------------------

def _events(db_conn, component):
    return [r[0] for r in db_conn.execute(text(
        "SELECT kind FROM service_event WHERE component = :c ORDER BY occurred_at, id"), {"c": component})]


def _cycle(database, redis_client, **targets):
    collector = _collector(database, redis_client, **targets)
    try:
        return collector.run_once()
    finally:
        collector.close()


def test_a_component_down_for_two_cycles_raises_one_down_event_and_one_recovered(database, redis_client, db_conn,
                                                                                http_server, closed_port):
    if not db_conn.execute(text("SELECT to_regclass('service_event') IS NOT NULL")).scalar():
        import pytest
        pytest.skip("017 not applied to this database")
    component = "catalog"
    _cycle(database, redis_client, catalog=closed_port)
    assert _events(db_conn, component) == []                      # one failed cycle: no alert (debounce)
    _cycle(database, redis_client, catalog=closed_port)
    assert _events(db_conn, component) == ["down"]                # second consecutive failure: one alert
    _cycle(database, redis_client, catalog=closed_port)
    assert _events(db_conn, component) == ["down"]                # still down: never a duplicate
    _cycle(database, redis_client, catalog=http_server(200, OK))
    assert _events(db_conn, component) == ["down", "recovered"]
    queued = db_conn.execute(text("""
        SELECT o.event_code FROM notification_outbox o JOIN service_event e ON e.id = o.source_id
        WHERE o.source_table = 'service_event' AND e.component = :c ORDER BY e.occurred_at"""), {"c": component}).scalars().all()
    assert queued == ["service.down", "service.recovered"]       # the trigger hands both to NotificationsService


def test_not_configured_components_never_alert(database, redis_client, db_conn):
    if not db_conn.execute(text("SELECT to_regclass('service_event') IS NOT NULL")).scalar():
        import pytest
        pytest.skip("017 not applied to this database")
    for _ in range(3):
        _cycle(database, redis_client)                            # every HTTP component is `unknown`
    assert _events(db_conn, "auth") == [] and _events(db_conn, "catalog") == []
