"""Phase E real concurrency (independent connections, committed data, injected hold after the lock).

review_env: correction_required session with open discrepancies d1, d2 and one reserved available piece.
"""

from __future__ import annotations

import time

from sqlalchemy import text

from review_fixtures import review_env  # noqa: F401  (fixture)
from test_concurrency import HOLD_SECONDS, _hold_after_lock, _run_parallel
from write_fixtures import committed_env  # noqa: F401  (fixture used by review_env)

DELAY = 0.1


def _scalar(pg_engine, sql, **params):
    with pg_engine.connect() as c:
        return c.execute(text(sql), params).scalar_one()


def _status(pg_engine, env):
    return _scalar(pg_engine, "SELECT s.code FROM work_session w JOIN cat_session_status s ON s.id = w.status_id "
                              "WHERE w.id = :s", s=env.session_id)


def _audit_count(pg_engine, env, action):
    return _scalar(pg_engine, "SELECT count(*) FROM access_audit WHERE resource_id = :s AND action = :a",
                   s=env.session_id, a=action)


def _resolve(env, discrepancy_id):
    url = f"/api/v1/sessions/{env.session_id}/discrepancies/{discrepancy_id}/resolve"
    body = {"notes": "Found in the tray", "outcome": "resolved"}
    return lambda c: c.post(url, json=body, headers={"Authorization": f"Bearer {env.spd_token}"})


def _close(env):
    url = f"/api/v1/sessions/{env.session_id}/close"
    return lambda c: c.post(url, json={"material_recovered": True}, headers={"Authorization": f"Bearer {env.spd_token}"})


def _later(call):
    return lambda c: (time.sleep(DELAY), call(c))[1]


def _hold_after_session_lock(monkeypatch):
    """Sleep right AFTER lock_session returns: the session row stays locked while the request holds."""
    from repositories import write_repository

    original = write_repository.lock_session

    def slow(*args, **kwargs):
        result = original(*args, **kwargs)
        time.sleep(HOLD_SECONDS)
        return result
    monkeypatch.setattr(write_repository, "lock_session", slow)


def _resolve_now(env, client_app, discrepancy_id):
    response = _resolve(env, discrepancy_id)(client_app.test_client())
    assert response.status_code == 200


def test_double_resolve_same_discrepancy(review_env, pg_engine, monkeypatch):
    env = review_env
    _hold_after_lock(monkeypatch, "count_open_discrepancies")
    responses = _run_parallel(env.app, [_resolve(env, env.d1)] * 2)
    assert sorted(r.status_code for r in responses) == [200, 409]
    loser = next(r for r in responses if r.status_code == 409)
    assert loser.get_json()["details"] == {"discrepancy_id": str(env.d1), "reason": "already_resolved"}
    assert loser.elapsed >= HOLD_SECONDS * 0.8, loser.elapsed             # it waited on the session lock
    assert _audit_count(pg_engine, env, "discrepancy.resolve") == 1
    description = _scalar(pg_engine, "SELECT description FROM discrepancy WHERE id = :d", d=env.d1)
    assert description.count("SPD resolution") == 1                       # applied exactly once
    assert _status(pg_engine, env) == "correction_required"


def test_two_discrepancies_resolved_concurrently_no_lost_update(review_env, pg_engine, monkeypatch):
    env = review_env
    _hold_after_lock(monkeypatch, "count_open_discrepancies")
    responses = _run_parallel(env.app, [_resolve(env, env.d1), _resolve(env, env.d2)])
    assert [r.status_code for r in responses] == [200, 200]
    results = sorted((r.get_json()["open_remaining"], r.get_json()["session_status_code"]) for r in responses)
    assert results == [(0, "awaiting_spd_review"), (1, "correction_required")]
    assert _status(pg_engine, env) == "awaiting_spd_review"
    assert _scalar(pg_engine, "SELECT count(*) FROM discrepancy WHERE session_id = :s AND NOT resolved",
                   s=env.session_id) == 0


def test_double_close_one_wins_side_effects_once(review_env, pg_engine, monkeypatch):
    env = review_env
    for disc in (env.d1, env.d2):
        _resolve_now(env, env.app, disc)
    _hold_after_lock(monkeypatch, "scoped_active_reservations")
    responses = _run_parallel(env.app, [_close(env)] * 2)
    assert sorted(r.status_code for r in responses) == [200, 409]
    loser = next(r for r in responses if r.status_code == 409)
    assert loser.get_json()["details"] == {"status_code": "closed", "reason": "not_awaiting_spd_review"}
    assert loser.elapsed >= HOLD_SECONDS * 0.8, loser.elapsed
    assert _scalar(pg_engine, "SELECT count(*) FROM count_event e JOIN cat_event_type t ON t.id = e.event_type_id "
                              "WHERE e.session_id = :s AND t.code = 'session_close'", s=env.session_id) == 1
    assert _audit_count(pg_engine, env, "session.spd_close") == 1
    assert _scalar(pg_engine, "SELECT count(*) FROM instrument_cycle_event WHERE instrument_id = :p", p=env.piece) == 1
    assert _scalar(pg_engine, "SELECT count(*) FROM instrument_reservation WHERE instrument_id = :p AND active",
                   p=env.piece) == 0


def test_resolve_wins_then_close_succeeds(review_env, pg_engine, monkeypatch):
    env = review_env
    _resolve_now(env, env.app, env.d1)                      # d2 is the last open one
    _hold_after_session_lock(monkeypatch)
    responses = _run_parallel(env.app, [_resolve(env, env.d2), _later(_close(env))])
    assert [r.status_code for r in responses] == [200, 200], [r.get_json() for r in responses]
    assert responses[0].get_json()["session_status_code"] == "awaiting_spd_review"
    assert responses[1].get_json()["status_code"] == "closed"
    assert _status(pg_engine, env) == "closed"


def test_close_wins_while_correction_required_then_resolve(review_env, pg_engine, monkeypatch):
    env = review_env
    _resolve_now(env, env.app, env.d1)
    _hold_after_session_lock(monkeypatch)
    responses = _run_parallel(env.app, [_close(env), _later(_resolve(env, env.d2))])
    assert [r.status_code for r in responses] == [409, 200], [r.get_json() for r in responses]
    assert responses[0].get_json()["details"] == {"status_code": "correction_required", "reason": "not_awaiting_spd_review"}
    assert responses[1].elapsed >= HOLD_SECONDS                     # waited for close's lock, then its own hold
    assert responses[1].get_json()["session_status_code"] == "awaiting_spd_review"
    assert _status(pg_engine, env) == "awaiting_spd_review"         # never closed with an open discrepancy
    assert _audit_count(pg_engine, env, "session.spd_close") == 0
    assert _scalar(pg_engine, "SELECT count(*) FROM instrument_cycle_event WHERE instrument_id = :p", p=env.piece) == 0
