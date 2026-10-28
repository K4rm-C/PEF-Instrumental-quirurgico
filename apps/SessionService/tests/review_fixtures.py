"""Fixtures for Phase E (resolve, close).

`rdata` (inside the rolled-back transaction, on top of `world`):
- `cr`: tenant A session in correction_required with two open discrepancies (`d1` family a, `d2`
  family b) and one resolved (`d3`); ended_at set by its count report;
- `aw`: tenant A session in awaiting_spd_review (0 open), ended_at = ENDED_AT, whose operation has
  reservations covering every DS03 case (see `pieces`/`reservations` below);
- `aw_legacy`: awaiting_spd_review with ended_at NULL;
- the SAME global families are stocked in tenant B, so cross-tenant leaks are observable.

`review_env` turns the committed Phase C environment into a correction_required session with two
open discrepancies and one reserved piece, for real-concurrency tests (deleted afterwards).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import text

from write_fixtures import committed_env  # noqa: F401  (fixture)

T0 = datetime(2026, 10, 14, 6, 0, tzinfo=timezone.utc)
ENDED_AT = T0 - timedelta(minutes=5)


def _run(conn, sql, **params):
    conn.execute(text(sql), params)


def _id(conn, table, code):
    return conn.execute(text(f"SELECT id FROM {table} WHERE code = :code"), {"code": code}).scalar_one()


def add_discrepancy(conn, session_id, family_id, *, resolved=False, reason="surplus", description="Extra piece"):
    """Generic (non physical-shortage) discrepancy by default: a note may resolve it. A `shortage` is a
    confirmed missing instrument and follows its own rules (tests/test_physical_shortage.py)."""
    disc_id = uuid.uuid4()
    detected = 1 if reason == "shortage" else 3
    _run(conn, "INSERT INTO discrepancy (id, description, resolved, resolved_at, reason_id, family_id, expected_quantity, "
               "detected_quantity, session_id, origin_event_id) VALUES (:id, :d, :r, :ra, "
               "(SELECT id FROM cat_discrepancy_reason WHERE code = :reason), :f, 2, :det, :s, NULL)",
         id=disc_id, d=description, r=resolved, ra=T0 if resolved else None, reason=reason, f=family_id, det=detected,
         s=session_id)
    return disc_id


@pytest.fixture
def rdata(world, db_conn):
    c, w = db_conn, world
    d = SimpleNamespace()
    op_status = _id(c, "cat_operation_status", "scheduled")
    phase = _id(c, "cat_operation_phase", "start")
    cycle = {code: _id(c, "cat_instrument_cycle_status", code)
             for code in ("available", "in_use", "lost", "maintenance", "reserved", "retired", "sterilization")}
    d.cycle = cycle

    def session(status_code, *, ended_at=ENDED_AT, inst_key="A", user_key="op_a"):
        operation_id, session_id = uuid.uuid4(), uuid.uuid4()
        _run(c, "INSERT INTO operation (id, scheduled_at, status_id, institution_id) VALUES (:id, :s, :st, :i)",
             id=operation_id, s=T0, st=op_status, i=w.institutions[inst_key])
        _run(c, "INSERT INTO work_session (id, started_at, ended_at, status_id, user_id, operation_id, station_id, kit_id, "
                "current_phase_id, capture_mode) VALUES (:id, :st, :en, (SELECT id FROM cat_session_status WHERE code = :sc), "
                ":u, :o, :stn, :k, :ph, 'manual_no_privacy')",
             id=session_id, st=T0 - timedelta(hours=1), en=ended_at, sc=status_code, u=w.users[user_key].id,
             o=operation_id, stn=w.stations[inst_key], k=w.kits[inst_key], ph=phase)
        return session_id, operation_id

    d.cr, d.cr_op = session("correction_required")
    d.d1 = add_discrepancy(c, d.cr, w.families["a"], description="Extra on Family a: expected 2, reported 3.")
    d.d2 = add_discrepancy(c, d.cr, w.families["b"], reason="surplus")
    d.d3 = add_discrepancy(c, d.cr, w.families["c"], resolved=True)
    d.aw, d.aw_op = session("awaiting_spd_review")
    d.aw_legacy, _ = session("awaiting_spd_review", ended_at=None)
    d.other_op = uuid.uuid4()
    _run(c, "INSERT INTO operation (id, status_id, institution_id) VALUES (:id, :st, :i)",
         id=d.other_op, st=op_status, i=w.institutions["A"])
    d.op_b = uuid.uuid4()
    _run(c, "INSERT INTO operation (id, status_id, institution_id) VALUES (:id, :st, :i)",
         id=d.op_b, st=op_status, i=w.institutions["B"])

    def piece(inst_key, suffix, status, active=True):
        piece_id = uuid.uuid4()
        _run(c, "INSERT INTO instrument (id, family_id, cycle_status_id, institution_id, active) VALUES (:id, :f, :s, :i, :a)",
             id=piece_id, f=w.families[suffix], s=cycle[status], i=w.institutions[inst_key], a=active)
        return piece_id

    def reservation(operation_id, piece_id, active=True):
        res_id = uuid.uuid4()
        _run(c, "INSERT INTO instrument_reservation (id, operation_id, instrument_id, reserved_at, released_at, active) "
                "VALUES (:id, :o, :p, :r, :rel, :a)", id=res_id, o=operation_id, p=piece_id, r=T0 - timedelta(days=1),
             rel=None if active else T0 - timedelta(hours=12), a=active)
        return res_id

    d.pieces = {
        "a_available": piece("A", "a", "available"),          # → sterilization
        "b_in_use": piece("A", "b", "in_use"),                # → sterilization
        "c_reserved": piece("A", "c", "reserved"),            # → sterilization
        "a_lost": piece("A", "a", "lost", active=False),      # released, unchanged
        "b_maintenance": piece("A", "b", "maintenance"),      # released, unchanged
        "c_inactive": piece("A", "c", "available", active=False),   # released, unchanged
        "a_old_res": piece("A", "a", "available"),            # reservation already released → untouched
        "a_other_op": piece("A", "a", "available"),           # reservation of another operation → untouched
        "a_B_reserved": piece("B", "a", "available"),         # tenant B piece reserved for A's operation → untouched
        "a_B_free": piece("B", "a", "available"),             # tenant B, same family, no reservation
        "a_B_other_op": piece("B", "a", "in_use"),            # tenant B reservation on B's operation
    }
    p = d.pieces
    d.reservations = {
        "a_available": reservation(d.aw_op, p["a_available"]),
        "b_in_use": reservation(d.aw_op, p["b_in_use"]),
        "c_reserved": reservation(d.aw_op, p["c_reserved"]),
        "a_lost": reservation(d.aw_op, p["a_lost"]),
        "b_maintenance": reservation(d.aw_op, p["b_maintenance"]),
        "c_inactive": reservation(d.aw_op, p["c_inactive"]),
        "a_old_res": reservation(d.aw_op, p["a_old_res"], active=False),
        "a_other_op": reservation(d.other_op, p["a_other_op"]),
        "a_B_reserved": reservation(d.aw_op, p["a_B_reserved"]),
        "a_B_other_op": reservation(d.op_b, p["a_B_other_op"]),
    }
    return d


def ds03_snapshot(conn):
    """Every instrument / reservation / cycle-event row, to prove what did (not) change."""
    return {
        "instrument": {r[0]: tuple(r[1:]) for r in conn.execute(text(
            "SELECT i.id, s.code, i.active, i.updated_at FROM instrument i "
            "JOIN cat_instrument_cycle_status s ON s.id = i.cycle_status_id"))},
        "reservation": {r[0]: tuple(r[1:]) for r in conn.execute(text(
            "SELECT id, active, released_at FROM instrument_reservation"))},
        "cycle_events": conn.execute(text("SELECT count(*) FROM instrument_cycle_event")).scalar_one(),
    }


# --- Committed data for real concurrency tests ---------------------------------------------------


@pytest.fixture
def review_env(committed_env, pg_engine):
    """committed_env's session in correction_required with discrepancies on families 0 and 1 and one
    reserved (available) piece of family 0 for the session's operation."""
    env = committed_env
    with pg_engine.begin() as c:
        env.operation_id = c.execute(text("SELECT operation_id FROM work_session WHERE id = :s"),
                                     {"s": env.session_id}).scalar_one()
        _run(c, "UPDATE work_session SET status_id = (SELECT id FROM cat_session_status WHERE code = 'correction_required'), "
                "capture_mode = 'manual_no_privacy', started_at = now() - interval '1 hour', ended_at = now() WHERE id = :s",
             s=env.session_id)
        env.d1 = add_discrepancy(c, env.session_id, env.families[0])
        env.d2 = add_discrepancy(c, env.session_id, env.families[1], reason="surplus")
        env.piece = c.execute(text("SELECT id FROM instrument WHERE family_id = :f ORDER BY id LIMIT 1"),
                              {"f": env.families[0]}).scalar_one()   # fixture setup only: any piece of the test tenant
        _run(c, "INSERT INTO instrument_reservation (operation_id, instrument_id, reserved_at) VALUES (:o, :p, now() - interval '1 day')",
             o=env.operation_id, p=env.piece)
    yield env   # committed_env's teardown deletes the session (cascades), operation (cascades reservations), pieces
