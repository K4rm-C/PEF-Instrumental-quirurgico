"""Real concurrency on SELECT ... FOR UPDATE (independent connections, committed data).

A short delay is injected *inside* the transaction (after the row lock) so the two requests are
guaranteed to overlap: the second one must block on the lock and then observe the committed state.
"""

from __future__ import annotations

import threading
import time

from sqlalchemy import text

from write_fixtures import committed_env  # noqa: F401  (fixture)

HOLD_SECONDS = 0.4


def _run_parallel(app, requests):
    """requests: list of callables(client) → response; all start together."""
    barrier = threading.Barrier(len(requests))
    results = [None] * len(requests)

    def worker(index, call):
        client = app.test_client()
        barrier.wait()
        started = time.perf_counter()
        response = call(client)
        response.elapsed = time.perf_counter() - started
        results[index] = response

    threads = [threading.Thread(target=worker, args=(i, call)) for i, call in enumerate(requests)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
    return results


def _hold_after_lock(monkeypatch, function_name):
    from repositories import write_repository

    original = getattr(write_repository, function_name)

    def slow(*args, **kwargs):
        time.sleep(HOLD_SECONDS)
        return original(*args, **kwargs)
    monkeypatch.setattr(write_repository, function_name, slow)


def test_concurrent_start_one_wins(committed_env, pg_engine, monkeypatch):
    env = committed_env
    _hold_after_lock(monkeypatch, "has_valid_agreement")   # runs after lock_session
    headers = {"Authorization": f"Bearer {env.op_token}"}
    url = f"/api/v1/sessions/{env.session_id}/start"
    responses = _run_parallel(env.app, [lambda c: c.post(url, headers=headers)] * 2)

    assert sorted(r.status_code for r in responses) == [200, 409]
    loser = next(r for r in responses if r.status_code == 409)
    assert loser.get_json()["details"] == {"status_code": "in_progress"}
    # The loser does nothing slow itself: it only took this long because it waited on the row lock.
    assert loser.elapsed >= HOLD_SECONDS * 0.8, loser.elapsed
    with pg_engine.connect() as c:
        status = c.execute(text("SELECT s.code FROM work_session w JOIN cat_session_status s ON s.id = w.status_id "
                                "WHERE w.id = :s"), {"s": env.session_id}).scalar_one()
        opens = c.execute(text("SELECT count(*) FROM count_event e JOIN cat_event_type t ON t.id = e.event_type_id "
                               "WHERE e.session_id = :s AND t.code = 'session_open'"), {"s": env.session_id}).scalar_one()
        audits = c.execute(text("SELECT count(*) FROM access_audit WHERE resource_id = :s AND action = 'session.start'"),
                           {"s": env.session_id}).scalar_one()
    assert (status, opens, audits) == ("in_progress", 1, 1)


def test_concurrent_inventory_replacements_never_mix(committed_env, pg_engine, monkeypatch):
    env = committed_env
    _hold_after_lock(monkeypatch, "available_stock")   # runs after lock_session, before the replacement
    f = env.families
    body_a = {"lines": [{"family_id": str(f[0]), "expected_quantity": 1}, {"family_id": str(f[1]), "expected_quantity": 2}]}
    body_b = {"lines": [{"family_id": str(f[2]), "expected_quantity": 3}, {"family_id": str(f[3]), "expected_quantity": 1}]}
    expected_a = {(f[0], 1), (f[1], 2)}
    expected_b = {(f[2], 3), (f[3], 1)}
    headers = {"Authorization": f"Bearer {env.spd_token}"}
    url = f"/api/v1/sessions/{env.session_id}/expected-inventory"
    for _ in range(3):
        responses = _run_parallel(env.app, [lambda c: c.put(url, json=body_a, headers=headers),
                                            lambda c: c.put(url, json=body_b, headers=headers)])
        assert [r.status_code for r in responses] == [200, 200]
        with pg_engine.connect() as c:
            rows = {(r[0], r[1]) for r in c.execute(text(
                "SELECT family_id, expected_quantity FROM expected_inventory WHERE session_id = :s"),
                {"s": env.session_id})}
        assert rows in (expected_a, expected_b), rows
    with pg_engine.connect() as c:
        audits = c.execute(text("SELECT count(*) FROM access_audit WHERE resource_id = :s "
                                "AND action = 'session.inventory_updated'"), {"s": env.session_id}).scalar_one()
    assert audits == 6


def test_start_blocks_while_inventory_update_holds_the_session(committed_env, pg_engine, monkeypatch):
    """Start and an inventory replacement serialize on the same work_session row."""
    env = committed_env
    _hold_after_lock(monkeypatch, "available_stock")
    f = env.families
    body = {"lines": [{"family_id": str(f[1]), "expected_quantity": 1}]}
    responses = _run_parallel(env.app, [
        lambda c: c.put(f"/api/v1/sessions/{env.session_id}/expected-inventory", json=body,
                        headers={"Authorization": f"Bearer {env.spd_token}"}),
        lambda c: (time.sleep(0.1), c.post(f"/api/v1/sessions/{env.session_id}/start",
                                           headers={"Authorization": f"Bearer {env.op_token}"}))[1],
    ])
    assert [r.status_code for r in responses] == [200, 200]
    with pg_engine.connect() as c:
        rows = {(r[0], r[1], r[2]) for r in c.execute(text(
            "SELECT family_id, expected_quantity, source FROM expected_inventory WHERE session_id = :s"),
            {"s": env.session_id})}
    # The replacement committed first (scheduled rules), then start kept the existing inventory.
    assert rows == {(f[1], 1, "schedule_additional")}
