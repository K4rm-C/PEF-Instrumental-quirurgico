"""Upload vs session close on REAL PostgreSQL locks (independent connections, committed data, full cleanup)."""

from __future__ import annotations

import io
import threading
import time
import uuid
from types import SimpleNamespace

import pytest
from sqlalchemy import text

from conftest import FakeStorage, TokenFactory, make_settings
from database import create_database
from main import create_app

JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 512 + b"\xff\xd9"
HOLD_SECONDS = 0.5


def _run(conn, sql, **params):
    conn.execute(text(sql), params)


@pytest.fixture
def committed(pg_engine, redis_client):
    tag = uuid.uuid4().hex[:8]
    env = SimpleNamespace(inst=uuid.uuid4(), role=uuid.uuid4(), user=uuid.uuid4(), operation=uuid.uuid4(),
                          session=uuid.uuid4())
    with pg_engine.begin() as c:
        _run(c, "INSERT INTO institution (id, name) VALUES (:id, :n)", id=env.inst, n=f"Evidence race {tag}")
        _run(c, "INSERT INTO role (id, code, description, institution_id) VALUES (:id, 'station_operator', 't', :i)",
             id=env.role, i=env.inst)
        _run(c, 'INSERT INTO "user" (id, name, email, password_hash, institution_id) VALUES (:id, :n, :e, \'x\', :i)',
             id=env.user, n=f"Race op {tag}", e=f"race.{tag}@evidence-test.invalid", i=env.inst)
        _run(c, "INSERT INTO user_role (user_id, role_id) VALUES (:u, :r)", u=env.user, r=env.role)
        _run(c, "INSERT INTO operation (id, status_id, institution_id) VALUES "
                "(:id, (SELECT id FROM cat_operation_status WHERE code = 'scheduled'), :i)", id=env.operation, i=env.inst)
        _run(c, "INSERT INTO work_session (id, status_id, user_id, operation_id, capture_mode, started_at) VALUES "
                "(:id, (SELECT id FROM cat_session_status WHERE code = 'in_progress'), :u, :o, 'vision', now())",
             id=env.session, u=env.user, o=env.operation)
    settings = make_settings()
    env.database = create_database(settings)          # real engine: independent connections and locks
    env.settings = settings
    tokens = TokenFactory(redis_client)
    env.token = tokens.issue(env.user, env.inst)
    env.redis = redis_client
    try:
        yield env
    finally:
        tokens.cleanup()
        env.database.engine.dispose()
        with pg_engine.begin() as c:
            _run(c, "DELETE FROM access_audit WHERE resource_id IN (SELECT id FROM media_asset WHERE session_id = :s)",
                 s=env.session)
            _run(c, "DELETE FROM media_asset WHERE session_id = :s", s=env.session)   # FK RESTRICT: assets first
            _run(c, "DELETE FROM work_session WHERE id = :s", s=env.session)
            _run(c, "DELETE FROM operation WHERE id = :o", o=env.operation)
            _run(c, "DELETE FROM user_role WHERE user_id = :u", u=env.user)
            _run(c, 'DELETE FROM "user" WHERE id = :u', u=env.user)
            _run(c, "DELETE FROM role WHERE id = :r", r=env.role)
            _run(c, "DELETE FROM institution WHERE id = :i", i=env.inst)


def _client(env, storage):
    app = create_app(env.settings, database=env.database, redis_client=env.redis, storage=storage)
    return app.test_client()


def _post(client, env):
    return client.post("/api/v1/evidence", content_type="multipart/form-data",
                       headers={"Authorization": f"Bearer {env.token}"},
                       data={"session_id": str(env.session), "kind": "jpeg", "file": (io.BytesIO(JPEG), "x.jpg")})


def _close_session(pg_engine, env):
    with pg_engine.begin() as c:
        _run(c, "UPDATE work_session SET status_id = (SELECT id FROM cat_session_status WHERE code = 'closed') "
                "WHERE id = :s", s=env.session)


def _counts(pg_engine, env):
    with pg_engine.connect() as c:
        assets = c.execute(text("SELECT count(*) FROM media_asset WHERE session_id = :s"), {"s": env.session}).scalar_one()
        audits = c.execute(text("SELECT count(*) FROM access_audit WHERE action = 'evidence.upload' AND resource_id IN "
                                "(SELECT id FROM media_asset WHERE session_id = :s)"), {"s": env.session}).scalar_one()
        status = c.execute(text("SELECT s.code FROM work_session w JOIN cat_session_status s ON s.id = w.status_id "
                                "WHERE w.id = :s"), {"s": env.session}).scalar_one()
    return assets, audits, status


def test_session_closed_while_uploading_is_rejected_and_blob_deleted(committed, pg_engine):
    """A: validated in_progress → PUT → (session committed as closed by another connection) → locked re-read: 409."""
    env = committed
    storage = FakeStorage(on_put=lambda: _close_session(pg_engine, env))
    response = _post(_client(env, storage), env)
    assert response.status_code == 409 and response.get_json()["details"] == {"status_code": "closed"}
    assert [c.split(":")[0] for c in storage.calls] == ["put", "head", "delete"]
    assert storage.objects == {}                                         # blob compensated
    assert _counts(pg_engine, env) == (0, 0, "closed")                  # no media_asset, no audit


def test_upload_lock_wins_close_waits_and_evidence_stays(committed, pg_engine, monkeypatch):
    """B: Evidence holds the work_session lock in its final transaction; a concurrent close blocks until COMMIT."""
    env = committed
    from adapters import audit_adapter

    original = audit_adapter.record
    closer = {}

    def close_in_background():
        started = time.perf_counter()
        _close_session(pg_engine, env)                                   # blocks on the row lock
        closer["waited"] = time.perf_counter() - started

    def record_then_hold(*args, **kwargs):
        original(*args, **kwargs)
        closer["thread"] = threading.Thread(target=close_in_background)
        closer["thread"].start()
        time.sleep(HOLD_SECONDS)                                         # still inside the locked transaction

    monkeypatch.setattr(audit_adapter, "record", record_then_hold)
    storage = FakeStorage()
    response = _post(_client(env, storage), env)
    closer["thread"].join(timeout=10)
    assert response.status_code == 201, response.get_data(as_text=True)
    assert closer["waited"] >= HOLD_SECONDS * 0.8, closer                # the close really waited for the lock
    assert _counts(pg_engine, env) == (1, 1, "closed")                  # evidence committed before the close
    assert len(storage.objects) == 1 and not any(c.startswith("delete") for c in storage.calls)
