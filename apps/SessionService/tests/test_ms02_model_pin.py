"""MS-02 / P1 in SessionService: POST /sessions/{id}/start pins the active YOLO model of a vision session.

Same rule as the portal's Start Session: set once in the locked start transaction, audited
(`session.model_pinned`), recorded in the session_open event, never overwritten (migration 011 trigger).
"""
from __future__ import annotations

import hashlib
import threading
import time
import uuid

import pytest
from sqlalchemy import text

from write_fixtures import committed_env, wdata  # noqa: F401 - fixtures

BASE = "/api/v1/sessions"


def _model(c, tag, active):
    model_id, sha = uuid.uuid4(), hashlib.sha256(tag.encode()).hexdigest()
    c.execute(text("INSERT INTO yolo_model (id, version_tag, checksum, active) VALUES (:i, :v, :c, :a)"),
              {"i": model_id, "v": f"ms02-{tag}", "c": sha, "a": active})
    return model_id, sha


@pytest.fixture
def pinned_world(world, db_conn):
    db_conn.execute(text("UPDATE yolo_model SET active = FALSE WHERE active"))
    world.model, world.model_sha = _model(db_conn, f"active-{world.tag}", True)
    world.other, _ = _model(db_conn, f"other-{world.tag}", False)
    session_id = world.sessions["a1"].id
    db_conn.execute(text("INSERT INTO session_processing_agreement (session_id, privacy_notice_version_id, "
                         "purpose_quality_ops) VALUES (:s, :n, TRUE)"), {"s": session_id, "n": world.notice_id})
    return world


def _pin(db_conn, session_id):
    return db_conn.execute(text("SELECT yolo_model_id FROM work_session WHERE id = :s"), {"s": session_id}).scalar()


def _pin_audits(db_conn, session_id):
    return db_conn.execute(text("SELECT count(*) FROM access_audit WHERE action = 'session.model_pinned' "
                                "AND resource_id = :s"), {"s": session_id}).scalar_one()


def test_start_pins_the_active_model(client, pinned_world, as_user, db_conn):
    w = pinned_world
    session_id = w.sessions["a1"].id
    response = client.post(f"{BASE}/{session_id}/start", json={}, headers=as_user("op_a"))
    assert response.status_code == 200, response.get_data(as_text=True)
    assert response.get_json()["capture_mode"] == "vision"
    assert _pin(db_conn, session_id) == w.model and _pin_audits(db_conn, session_id) == 1
    payload = db_conn.execute(text("SELECT e.payload FROM count_event e JOIN cat_event_type t ON t.id = e.event_type_id "
                                   "WHERE e.session_id = :s AND t.code = 'session_open'"), {"s": session_id}).scalar_one()
    assert (payload["yolo_model_id"], payload["model_sha256"]) == (str(w.model), w.model_sha)
    # Activating another model later never touches the started session (also refused by the database).
    db_conn.execute(text("UPDATE yolo_model SET active = FALSE WHERE id = :i"), {"i": w.model})
    db_conn.execute(text("UPDATE yolo_model SET active = TRUE WHERE id = :i"), {"i": w.other})
    assert _pin(db_conn, session_id) == w.model
    nested = db_conn.begin_nested()
    with pytest.raises(Exception, match="pinned model cannot change"):
        db_conn.execute(text("UPDATE work_session SET yolo_model_id = :m WHERE id = :s"), {"m": w.other, "s": session_id})
    nested.rollback()


def test_start_without_active_model_leaves_the_pin_to_the_first_bind(client, pinned_world, as_user, db_conn):
    db_conn.execute(text("UPDATE yolo_model SET active = FALSE WHERE active"))
    session_id = pinned_world.sessions["a1"].id
    response = client.post(f"{BASE}/{session_id}/start", json={}, headers=as_user("op_a"))
    assert response.status_code == 200 and _pin(db_conn, session_id) is None and _pin_audits(db_conn, session_id) == 0


def test_manual_session_pins_nothing(client, world, as_user, db_conn):
    session_id = world.sessions["a1"].id          # no agreement: manual_no_privacy
    response = client.post(f"{BASE}/{session_id}/start", json={}, headers=as_user("op_a"))
    assert response.status_code == 200 and response.get_json()["capture_mode"] == "manual_no_privacy"
    assert _pin(db_conn, session_id) is None


def test_concurrent_starts_pin_exactly_once(committed_env, pg_engine, monkeypatch):
    """Two real, overlapping POST /start on committed data: one wins, the model is pinned and audited once."""
    env = committed_env
    with pg_engine.begin() as c:
        notice = c.execute(text("SELECT id FROM privacy_notice_version WHERE active AND effective_at <= now() "
                                "ORDER BY effective_at LIMIT 1")).scalar()
        if notice is None:
            pytest.skip("no active privacy notice in force in the test database")
        c.execute(text("INSERT INTO session_processing_agreement (session_id, privacy_notice_version_id, "
                       "purpose_quality_ops) VALUES (:s, :n, TRUE)"), {"s": env.session_id, "n": notice})
        active = c.execute(text("SELECT id FROM yolo_model WHERE active")).scalar()
    if active is None:
        pytest.skip("no active YOLO model in the test database")
    from repositories import write_repository
    original = write_repository.has_valid_agreement

    def slow(*args, **kwargs):          # runs after lock_session: both requests overlap on the row lock
        time.sleep(0.4)
        return original(*args, **kwargs)
    monkeypatch.setattr(write_repository, "has_valid_agreement", slow)
    url, headers = f"{BASE}/{env.session_id}/start", {"Authorization": f"Bearer {env.op_token}"}
    barrier, results = threading.Barrier(2), []

    def call():
        client = env.app.test_client()
        barrier.wait()
        results.append(client.post(url, headers=headers, json={}).status_code)
    threads = [threading.Thread(target=call) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
    with pg_engine.connect() as c:
        pinned = c.execute(text("SELECT yolo_model_id FROM work_session WHERE id = :s"), {"s": env.session_id}).scalar()
        audits = c.execute(text("SELECT count(*) FROM access_audit WHERE action = 'session.model_pinned' "
                                "AND resource_id = :s"), {"s": env.session_id}).scalar_one()
    with pg_engine.begin() as c:
        c.execute(text("DELETE FROM session_processing_agreement WHERE session_id = :s"), {"s": env.session_id})
    assert sorted(results) == [200, 409]
    assert pinned == active and audits == 1


def test_detail_exposes_the_pinned_model(client, pinned_world, as_user, db_conn):
    """MS-03: the Supervisor's session detail carries the pinned model (id, version, SHA-256) and the
    session_open event keeps its identity; a session without pin answers vision_model = null."""
    w = pinned_world
    session_id = w.sessions["a1"].id
    client.post(f"{BASE}/{session_id}/start", json={}, headers=as_user("op_a"))
    detail = client.get(f"{BASE}/{session_id}?expand=detail", headers=as_user("spd_a")).get_json()
    assert detail["vision_model"] == {"id": str(w.model), "version_tag": f"ms02-active-{w.tag}", "sha256": w.model_sha}
    opened = next(e for e in detail["events"] if e["event_code"] == "session_open")
    assert opened["payload"]["model_sha256"] == w.model_sha and opened["payload"]["yolo_model_id"] == str(w.model)
    other = client.get(f"{BASE}/{w.sessions['a3'].id}?expand=detail", headers=as_user("spd_a")).get_json()
    assert other["vision_model"] is None
