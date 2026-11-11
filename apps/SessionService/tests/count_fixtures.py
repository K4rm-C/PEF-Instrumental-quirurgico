"""Fixtures for Phase D (vision-counts, count-report).

`cdata` builds on `world` inside the rolled-back transaction: in-progress sessions of tenant A
assigned to op_a, one `vision` and one `manual_no_privacy`, each with its expected inventory.
`counting_env` turns the committed Phase C environment into an in-progress vision session (plus a
second session of the same tenant) for real-concurrency tests, and deletes everything afterwards.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import text

from write_fixtures import committed_env  # noqa: F401  (fixture)

T0 = datetime(2026, 10, 14, 5, 0, tzinfo=timezone.utc)


def _run(conn, sql, **params):
    conn.execute(text(sql), params)


def _id(conn, table, code):
    return conn.execute(text(f"SELECT id FROM {table} WHERE code = :code"), {"code": code}).scalar_one()


def set_status(conn, session_id, code):
    _run(conn, "UPDATE work_session SET status_id = (SELECT id FROM cat_session_status WHERE code = :c) WHERE id = :s",
         c=code, s=session_id)


def add_auto_count(conn, session_id, trigger, counts, *, minute=0, occurred_at=None, client_event_id=None):
    """Directly stored batch (the shape Session itself writes)."""
    event_id = uuid.uuid4()
    payload = {"model_version": "yolo26l-demo", "trigger": trigger,
               "counts": [{"family_id": str(f), "detected_quantity": q} for f, q in counts]}
    _run(conn, "INSERT INTO count_event (id, event_type_id, client_event_id, payload, occurred_at, session_id) "
               "VALUES (:id, (SELECT id FROM cat_event_type WHERE code = 'auto_count'), :ce, CAST(:p AS jsonb), :o, :s)",
         id=event_id, ce=client_event_id or uuid.uuid4(), p=json.dumps(payload),
         o=occurred_at or T0 + timedelta(minutes=minute), s=session_id)
    return event_id


@pytest.fixture
def cdata(world, db_conn):
    c, w = db_conn, world
    d = SimpleNamespace()
    status = _id(c, "cat_session_status", "in_progress")
    op_status = _id(c, "cat_operation_status", "scheduled")
    phase = _id(c, "cat_operation_phase", "start")

    def session(capture_mode, lines, user_key="op_a", inst_key="A"):
        operation_id, session_id = uuid.uuid4(), uuid.uuid4()
        _run(c, "INSERT INTO operation (id, scheduled_at, status_id, institution_id) VALUES (:id, :s, :st, :i)",
             id=operation_id, s=T0, st=op_status, i=w.institutions[inst_key])
        _run(c, "INSERT INTO work_session (id, started_at, status_id, user_id, operation_id, station_id, kit_id, "
                "current_phase_id, capture_mode) VALUES (:id, :st, :s, :u, :o, :stn, :k, :ph, :cm)",
             id=session_id, st=T0 - timedelta(minutes=30), s=status, u=w.users[user_key].id, o=operation_id,
             stn=w.stations[inst_key], k=w.kits[inst_key], ph=phase, cm=capture_mode)
        for suffix, qty in lines:
            _run(c, "INSERT INTO expected_inventory (session_id, family_id, expected_quantity, source) "
                    "VALUES (:s, :f, :q, 'kit_snapshot')", s=session_id, f=w.families[suffix], q=qty)
        return session_id

    d.vision = session("vision", [("a", 2), ("b", 1), ("c", 3)])
    d.manual = session("manual_no_privacy", [("a", 2), ("b", 1)])
    d.vision_b = session("vision", [("a", 1)], user_key="op_b", inst_key="B")
    return d


def vision_body(world, *counts, trigger="final", client_event_id=None, occurred_at="2026-10-14T05:00:00Z",
                model_version="yolo26l-demo"):
    return {
        "client_event_id": str(client_event_id or uuid.uuid4()),
        "occurred_at": occurred_at,
        "model_version": model_version,
        "trigger": trigger,
        "counts": [{"family_id": str(world.families[s]), "detected_quantity": q} for s, q in counts],
    }


def report_line(world, suffix, reported, suggested=None, reason_code=None, notes=None):
    return {"family_id": str(world.families[suffix]), "suggested_quantity": suggested,
            "reported_quantity": reported, "reason_code": reason_code, "notes": notes}


# --- Committed data for real concurrency tests ---------------------------------------------------


@pytest.fixture
def counting_env(committed_env, pg_engine):
    """committed_env's session as in_progress + vision (expected: family 0 ×1), plus a second session."""
    env = committed_env
    env.session_2 = uuid.uuid4()
    with pg_engine.begin() as c:
        _run(c, "UPDATE work_session SET status_id = :s, capture_mode = 'vision', started_at = now() WHERE id = :id",
             s=_id(c, "cat_session_status", "in_progress"), id=env.session_id)
        _run(c, "INSERT INTO work_session (id, status_id, user_id, operation_id, station_id, kit_id, capture_mode, "
                "started_at) SELECT :id, status_id, user_id, operation_id, station_id, kit_id, capture_mode, now() "
                "FROM work_session WHERE id = :src", id=env.session_2, src=env.session_id)
        _run(c, "INSERT INTO expected_inventory (session_id, family_id, expected_quantity) VALUES (:s, :f, 1)",
             s=env.session_2, f=env.families[0])
    try:
        yield env
    finally:
        with pg_engine.begin() as c:
            _run(c, "DELETE FROM access_audit WHERE resource_id = :s", s=env.session_2)
            _run(c, "DELETE FROM work_session WHERE id = :s", s=env.session_2)   # cascades lines/events/HC/discrepancies
