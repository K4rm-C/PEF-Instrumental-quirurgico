"""DS03 §1.3.9.1 lifecycle as applied by Session v1 (Phase E.1): catalog codes, close → sterilization,
and a real race between resolve(lost) and close on the same piece/reservation (lock order)."""

from __future__ import annotations

import time
import uuid

import pytest
from sqlalchemy import text

from review_fixtures import add_discrepancy, rdata, review_env  # noqa: F401  (fixtures)
from services import session_review
from test_concurrency import HOLD_SECONDS, _run_parallel
from write_fixtures import committed_env  # noqa: F401  (fixture used by review_env)

DS03_CYCLE_CODES = {"available", "reserved", "in_use", "sterilization", "maintenance", "retired", "lost"}


def test_rules_use_only_real_catalog_codes(db_conn):
    codes = set(db_conn.execute(text("SELECT code FROM cat_instrument_cycle_status")).scalars())
    assert codes == DS03_CYCLE_CODES
    used = set(session_review.IN_SERVICE_STATES) | {session_review.LOST, session_review.AVAILABLE,
                                                     session_review.STERILIZATION}
    assert used <= codes


@pytest.mark.parametrize("status, active, expected", [
    ("available", True, ("sterilization", True)),
    ("reserved", True, ("sterilization", True)),
    ("in_use", True, ("sterilization", True)),
    ("lost", False, ("lost", False)),
    ("maintenance", True, ("maintenance", True)),
    ("sterilization", True, ("sterilization", True)),
    ("retired", False, ("retired", False)),
    ("available", False, ("available", False)),
    ("in_use", False, ("in_use", False)),
])
def test_close_transition_per_origin(client, world, rdata, as_user, db_conn, status, active, expected):
    piece, res = uuid.uuid4(), uuid.uuid4()
    db_conn.execute(text("INSERT INTO instrument (id, family_id, cycle_status_id, institution_id, active) "
                         "VALUES (:id, :f, :s, :i, :a)"),
                    {"id": piece, "f": world.families["d"], "s": rdata.cycle[status], "i": world.institutions["A"],
                     "a": active})
    db_conn.execute(text("INSERT INTO instrument_reservation (id, operation_id, instrument_id, reserved_at) "
                         "VALUES (:id, :o, :p, now() - interval '1 day')"), {"id": res, "o": rdata.aw_op, "p": piece})
    response = client.post(f"/api/v1/sessions/{rdata.aw}/close", json={"material_recovered": True},
                           headers=as_user("spd_a"))
    assert response.status_code == 200
    state = db_conn.execute(text("SELECT s.code, i.active FROM instrument i JOIN cat_instrument_cycle_status s "
                                 "ON s.id = i.cycle_status_id WHERE i.id = :p"), {"p": piece}).one()
    assert tuple(state) == expected
    events = db_conn.execute(text("SELECT count(*) FROM instrument_cycle_event WHERE instrument_id = :p"),
                             {"p": piece}).scalar_one()
    transitioned = expected[0] == "sterilization" and status != "sterilization"
    assert events == (1 if transitioned else 0)
    assert db_conn.execute(text("SELECT active FROM instrument_reservation WHERE id = :r"), {"r": res}).scalar_one() is False


# --- resolve(lost) vs close on the same piece -----------------------------------------------------------


@pytest.fixture
def shared_piece_env(review_env, pg_engine):
    """review_env.session_id (correction_required, d1 on family 0) + a second session of the SAME operation in
    awaiting_spd_review. env.piece (family 0) is reserved for that operation: both requests target it."""
    env = review_env
    env.closing = uuid.uuid4()
    with pg_engine.begin() as c:
        c.execute(text("INSERT INTO work_session (id, status_id, user_id, operation_id, station_id, kit_id, "
                       "capture_mode, started_at, ended_at) SELECT :id, (SELECT id FROM cat_session_status WHERE code = "
                       "'awaiting_spd_review'), user_id, operation_id, station_id, kit_id, capture_mode, started_at, "
                       "ended_at FROM work_session WHERE id = :src"), {"id": env.closing, "src": env.session_id})
    try:
        yield env
    finally:
        with pg_engine.begin() as c:
            c.execute(text("DELETE FROM access_audit WHERE resource_id = :s"), {"s": env.closing})
            c.execute(text("DELETE FROM work_session WHERE id = :s"), {"s": env.closing})


def _hold_after_instrument_lock(monkeypatch):
    from repositories import write_repository

    original = write_repository.lock_instruments

    def slow(*args, **kwargs):
        result = original(*args, **kwargs)
        time.sleep(HOLD_SECONDS)       # keeps the instrument row locked before reservations are locked
        return result
    monkeypatch.setattr(write_repository, "lock_instruments", slow)


def _calls(env):
    headers = {"Authorization": f"Bearer {env.spd_token}"}
    resolve = lambda c: c.post(f"/api/v1/sessions/{env.session_id}/discrepancies/{env.d1}/resolve",  # noqa: E731
                               json={"notes": "Not found", "outcome": "lost", "instrument_id": str(env.piece)},
                               headers=headers)
    close = lambda c: c.post(f"/api/v1/sessions/{env.closing}/close", json={"material_recovered": True},  # noqa: E731
                             headers=headers)
    return resolve, close


def _piece(pg_engine, env):
    with pg_engine.connect() as c:
        state = c.execute(text("SELECT s.code, i.active FROM instrument i JOIN cat_instrument_cycle_status s "
                               "ON s.id = i.cycle_status_id WHERE i.id = :p"), {"p": env.piece}).one()
        events = c.execute(text("SELECT count(*) FROM instrument_cycle_event WHERE instrument_id = :p"),
                           {"p": env.piece}).scalar_one()
        active_res = c.execute(text("SELECT count(*) FROM instrument_reservation WHERE instrument_id = :p AND active"),
                               {"p": env.piece}).scalar_one()
    return tuple(state), events, active_res


def test_resolve_lost_first_then_close_skips_the_piece(shared_piece_env, pg_engine, monkeypatch):
    env = shared_piece_env
    _hold_after_instrument_lock(monkeypatch)
    resolve, close = _calls(env)
    responses = _run_parallel(env.app, [resolve, lambda c: (time.sleep(0.1), close(c))[1]])
    assert [r.status_code for r in responses] == [200, 200], [r.get_json() for r in responses]   # no deadlock
    assert _piece(pg_engine, env) == (("lost", False), 1, 0)          # close saw the released reservation
    with pg_engine.connect() as c:
        payload = c.execute(text("SELECT e.payload FROM count_event e JOIN cat_event_type t ON t.id = e.event_type_id "
                                 "WHERE e.session_id = :s AND t.code = 'session_close'"), {"s": env.closing}).scalar_one()
    assert payload == {"closed_by": "spd_supervisor", "material_recovered": True, "safe_close_confirmed": True, "contingency_reviewed": False, "released_reservations": 0, "sterilized_instruments": 0, "unreconciled_physical_shortages": []}


def test_close_first_then_resolve_lost_is_409(shared_piece_env, pg_engine, monkeypatch):
    env = shared_piece_env
    _hold_after_instrument_lock(monkeypatch)
    resolve, close = _calls(env)
    responses = _run_parallel(env.app, [close, lambda c: (time.sleep(0.1), resolve(c))[1]])
    assert [r.status_code for r in responses] == [200, 409], [r.get_json() for r in responses]   # no deadlock
    assert responses[1].get_json()["details"] == {"cycle_status_code": "sterilization", "active": True}
    assert _piece(pg_engine, env) == (("sterilization", True), 1, 0)
