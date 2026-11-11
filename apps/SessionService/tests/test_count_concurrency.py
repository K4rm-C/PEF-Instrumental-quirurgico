"""Phase D real concurrency (independent connections, committed data, injected hold after the lock).

counting_env: session_id and session_2 are in_progress + vision, operator = env.operator, expected
inventory: family 0 ×1 each.
"""

from __future__ import annotations

import time
import uuid

from sqlalchemy import text

from count_fixtures import counting_env  # noqa: F401  (fixture)
from write_fixtures import committed_env  # noqa: F401  (fixture used by counting_env)
from test_concurrency import HOLD_SECONDS, _hold_after_lock, _run_parallel

DELAY = 0.1   # the second request starts slightly later so the first one surely takes the lock


def _scalar(pg_engine, sql, **params):
    with pg_engine.connect() as c:
        return c.execute(text(sql), params).scalar_one()


def _event_count(pg_engine, session_id, code):
    return _scalar(pg_engine, "SELECT count(*) FROM count_event e JOIN cat_event_type t ON t.id = e.event_type_id "
                              "WHERE e.session_id = :s AND t.code = :c", s=session_id, c=code)


def _status(pg_engine, session_id):
    return _scalar(pg_engine, "SELECT s.code FROM work_session w JOIN cat_session_status s ON s.id = w.status_id "
                              "WHERE w.id = :s", s=session_id)


def _vision(env, quantity, client_event_id, session_id=None):
    body = {"client_event_id": str(client_event_id), "occurred_at": "2026-10-14T05:00:00Z",
            "model_version": "yolo26l-demo", "trigger": "final",
            "counts": [{"family_id": str(env.families[0]), "detected_quantity": quantity}]}
    url = f"/api/v1/sessions/{session_id or env.session_id}/vision-counts"
    return lambda c: c.post(url, json=body, headers={"Authorization": f"Bearer {env.op_token}"})


def _report(env, reported, suggested=None, notes=None, family=0, reason=None):
    body = {"reports": [{"family_id": str(env.families[family]), "reported_quantity": reported,
                         "suggested_quantity": suggested, "notes": notes, "reason_code": reason}]}
    url = f"/api/v1/sessions/{env.session_id}/count-report"
    return lambda c: c.post(url, json=body, headers={"Authorization": f"Bearer {env.op_token}"})


def _later(call):
    return lambda c: (time.sleep(DELAY), call(c))[1]


# --- Idempotency under concurrency --------------------------------------------------------------


def test_identical_concurrent_vision_counts_201_and_200(counting_env, pg_engine, monkeypatch):
    env = counting_env
    _hold_after_lock(monkeypatch, "expected_rows")
    client_event_id = uuid.uuid4()
    responses = _run_parallel(env.app, [_vision(env, 1, client_event_id)] * 2)
    assert sorted(r.status_code for r in responses) == [200, 201]
    assert responses[0].get_json()["count_event_id"] == responses[1].get_json()["count_event_id"]
    assert _event_count(pg_engine, env.session_id, "auto_count") == 1


def test_conflicting_concurrent_vision_counts_201_and_409(counting_env, pg_engine, monkeypatch):
    env = counting_env
    _hold_after_lock(monkeypatch, "expected_rows")
    client_event_id = uuid.uuid4()
    responses = _run_parallel(env.app, [_vision(env, 1, client_event_id), _vision(env, 2, client_event_id)])
    assert sorted(r.status_code for r in responses) == [201, 409]
    assert _event_count(pg_engine, env.session_id, "auto_count") == 1


def test_same_client_event_id_on_two_sessions_relies_on_unique_constraint(counting_env, pg_engine, monkeypatch):
    """Different sessions → different row locks: both pass the lookup, the UNIQUE insert decides."""
    env = counting_env
    _hold_after_lock(monkeypatch, "expected_rows")   # both requests look up (nothing), then hold, then insert
    client_event_id = uuid.uuid4()
    responses = _run_parallel(env.app, [_vision(env, 1, client_event_id),
                                        _vision(env, 1, client_event_id, session_id=env.session_2)])
    assert sorted(r.status_code for r in responses) == [201, 409]
    total = _scalar(pg_engine, "SELECT count(*) FROM count_event WHERE client_event_id = :c", c=client_event_id)
    assert total == 1


# --- Count report -------------------------------------------------------------------------------


def test_double_count_report_one_wins(counting_env, pg_engine, monkeypatch):
    env = counting_env
    _hold_after_lock(monkeypatch, "latest_final_auto_count")
    responses = _run_parallel(env.app, [_report(env, 0, reason="occluded")] * 2)
    assert sorted(r.status_code for r in responses) == [200, 409]
    loser = next(r for r in responses if r.status_code == 409)
    assert loser.get_json()["details"] == {"status_code": "correction_required"}
    assert loser.elapsed >= HOLD_SECONDS * 0.8, loser.elapsed      # it waited on the row lock
    assert _event_count(pg_engine, env.session_id, "manual_count") == 1
    assert _event_count(pg_engine, env.session_id, "manual_close") == 1
    assert _scalar(pg_engine, "SELECT count(*) FROM discrepancy WHERE session_id = :s", s=env.session_id) == 1
    assert _scalar(pg_engine, "SELECT count(*) FROM access_audit WHERE resource_id = :s "
                              "AND action = 'session.manual_close'", s=env.session_id) == 1


def test_vision_final_first_then_report_validates_against_it(counting_env, pg_engine, monkeypatch):
    env = counting_env
    _hold_after_lock(monkeypatch, "expected_rows")
    # The report claims AI = 2: only valid if it sees the final committed by the vision request.
    responses = _run_parallel(env.app, [_vision(env, 2, uuid.uuid4()),
                                        _later(_report(env, 1, suggested=2, notes="Only one piece on the tray"))])
    assert [r.status_code for r in responses] == [201, 200], [r.get_json() for r in responses]
    assert responses[1].get_json()["human_corrections"] == 1
    assert _status(pg_engine, env.session_id) == "awaiting_spd_review"


def test_report_first_then_vision_is_409(counting_env, pg_engine, monkeypatch):
    env = counting_env
    _hold_after_lock(monkeypatch, "latest_final_auto_count")
    responses = _run_parallel(env.app, [_report(env, 1), _later(_vision(env, 1, uuid.uuid4()))])
    assert [r.status_code for r in responses] == [200, 409]
    assert responses[1].get_json()["details"] == {"status_code": "awaiting_spd_review"}
    assert responses[1].elapsed >= (HOLD_SECONDS - DELAY) * 0.8
    assert _event_count(pg_engine, env.session_id, "auto_count") == 0   # never after leaving in_progress


def test_inventory_update_first_then_report_uses_new_expected(counting_env, pg_engine, monkeypatch):
    env = counting_env
    _hold_after_lock(monkeypatch, "available_stock")      # PUT holds the session lock
    put_body = {"lines": [{"family_id": str(env.families[1]), "expected_quantity": 2}]}
    put = lambda c: c.put(f"/api/v1/sessions/{env.session_id}/expected-inventory", json=put_body,  # noqa: E731
                          headers={"Authorization": f"Bearer {env.spd_token}"})
    responses = _run_parallel(env.app, [put, _later(_report(env, 2, family=1))])
    # With the old inventory (family 0) this report would be 422 (extra) — it saw the replacement.
    assert [r.status_code for r in responses] == [200, 200], [r.get_json() for r in responses]
    with pg_engine.connect() as c:
        rows = c.execute(text("SELECT family_id, expected_quantity, detected_quantity FROM count_event e "
                              "JOIN cat_event_type t ON t.id = e.event_type_id WHERE e.session_id = :s "
                              "AND t.code = 'manual_count'"), {"s": env.session_id}).all()
    assert [tuple(r) for r in rows] == [(env.families[1], 2, 2)]


def test_report_first_then_inventory_update_is_409(counting_env, pg_engine, monkeypatch):
    env = counting_env
    _hold_after_lock(monkeypatch, "latest_final_auto_count")
    put_body = {"lines": [{"family_id": str(env.families[1]), "expected_quantity": 2}]}
    put = lambda c: c.put(f"/api/v1/sessions/{env.session_id}/expected-inventory", json=put_body,  # noqa: E731
                          headers={"Authorization": f"Bearer {env.spd_token}"})
    responses = _run_parallel(env.app, [_report(env, 1), _later(put)])
    assert [r.status_code for r in responses] == [200, 409]
    assert responses[1].get_json()["details"] == {"status_code": "awaiting_spd_review", "reason": "inventory_locked"}
    with pg_engine.connect() as c:
        rows = c.execute(text("SELECT family_id, expected_quantity FROM expected_inventory WHERE session_id = :s"),
                         {"s": env.session_id}).all()
    assert [tuple(r) for r in rows] == [(env.families[0], 1)]       # frozen, never mixed
