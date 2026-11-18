"""MS-07 idempotent operations: forwarding with the caller's token, replay, mismatch, concurrency, uncertain
outcomes reconciled from the owner's audit trail, refused operations. The owner services are local HTTP
doubles here; the real SessionService / CatalogService are used by scripts/e2e_ms07_sync_fhir.py."""

from __future__ import annotations

import hashlib
import threading
import uuid

import pytest
from sqlalchemy import text

from main import create_app
from tests.conftest import make_settings

URL = "/api/v1/sync/operations"


@pytest.fixture
def ops(database, redis_client, upstream):
    def build(script):
        server = upstream(script)
        app = create_app(make_settings(upstreams={"session": server.url, "catalog": server.url,
                                                  "notifications": server.url}),
                         database=database, redis_client=redis_client)
        return app.test_client(), server
    return build


def post(client, headers, key, body):
    return client.post(URL, json=body, headers={**headers, "Idempotency-Key": key})


SCHEDULE = {"type": "session.schedule", "payload": {"kit_id": "k", "station_id": "s"}}


def test_forwarded_once_and_replayed(ops, as_user, db_conn, world):
    client, server = ops(lambda m, p, n: (201, {"id": str(uuid.uuid4()), "status": {"code": "scheduled"}}, 0))
    headers = as_user("spd_a")
    first = post(client, headers, "key-0000001", SCHEDULE)
    second = post(client, headers, "key-0000001", SCHEDULE)
    assert first.status_code == second.status_code == 201 and first.get_json() == second.get_json()
    assert first.headers["Idempotent-Replayed"] == "false" and second.headers["Idempotent-Replayed"] == "true"
    assert len(server.calls) == 1                                       # applied exactly once
    call = server.calls[0]
    assert call["method"] == "POST" and call["path"] == "/api/v1/sessions/schedule"
    assert call["headers"]["Authorization"] == headers["Authorization"]          # the caller's own token
    assert call["headers"]["X-Correlation-ID"].startswith("op-") and call["body"] == SCHEDULE["payload"]
    actions = db_conn.execute(text("SELECT action, actor_user_id FROM access_audit WHERE correlation_id = :c"),
                              {"c": call["headers"]["X-Correlation-ID"]}).all()
    assert actions == [("SYNC_OPERATION_SESSION_SCHEDULE", world.users["spd_a"].id)]


def test_same_key_other_request_and_other_user(ops, as_user):
    client, server = ops(lambda m, p, n: (201, {"id": str(uuid.uuid4())}, 0))
    post(client, as_user("spd_a"), "key-0000002", SCHEDULE)
    changed = post(client, as_user("spd_a"), "key-0000002", {**SCHEDULE, "payload": {"kit_id": "other"}})
    assert changed.status_code == 422 and changed.get_json()["error"] == "idempotency_key_mismatch"
    other_user = post(client, as_user("spd_b"), "key-0000002", SCHEDULE)       # keys are per principal
    assert other_user.status_code == 201 and len(server.calls) == 2


def test_business_errors_are_stored_but_credentials_errors_are_not(ops, as_user):
    answers = iter([(409, {"error": "version_conflict", "details": {"current_version": 3}}, 0),
                    (401, {"error": "unauthorized"}, 0), (200, {"id": str(uuid.uuid4())}, 0)])
    client, server = ops(lambda m, p, n: next(answers))
    body = {"type": "tray.update", "target_id": str(uuid.uuid4()), "payload": {"version": 1, "name": "x"}}
    conflict = post(client, as_user("spd_a"), "key-0000003", body)
    assert conflict.status_code == 409 and post(client, as_user("spd_a"), "key-0000003", body).status_code == 409
    assert len(server.calls) == 1                                   # a version conflict is final: replayed
    expired = post(client, as_user("spd_a"), "key-0000004", {**body, "payload": {"version": 3}})
    assert expired.status_code == 401
    retried = post(client, as_user("spd_a"), "key-0000004", {**body, "payload": {"version": 3}})
    assert retried.status_code == 200 and len(server.calls) == 3    # 401 was not stored: retried with a new token


def test_uncertain_outcome_is_reconciled_from_the_owner_audit(ops, as_user, db_conn, world):
    client, server = ops(lambda m, p, n: (500, {"error": "internal_error"}, 0))
    headers = as_user("spd_a")
    first = post(client, headers, "key-0000005", SCHEDULE)
    assert first.status_code == 502 and first.get_json()["error"] == "operation_outcome_unknown"
    # The owner had actually scheduled it (its audit row carries our correlation id).
    correlation = server.calls[0]["headers"]["X-Correlation-ID"]
    created = uuid.uuid4()
    db_conn.execute(text("INSERT INTO access_audit (id, occurred_at, actor_type, actor_user_id, action, resource_type, "
                         "resource_id, institution_id, outcome, correlation_id) VALUES (gen_random_uuid(), now(), 'user', "
                         ":u, 'session.scheduled', 'work_session', :r, :i, 'success', :c)"),
                    {"u": world.users["spd_a"].id, "r": created, "i": world.inst["A"], "c": correlation})
    second = post(client, headers, "key-0000005", SCHEDULE)
    assert second.status_code == 200 and second.get_json() == {"id": str(created), "recovered": True}
    assert second.headers["Idempotent-Recovered"] == "true" and len(server.calls) == 1   # never applied twice


def test_uncertain_outcome_without_owner_trace_is_forwarded_again(ops, as_user):
    answers = iter([(502, {}, 0), (201, {"id": str(uuid.uuid4())}, 0)])
    client, server = ops(lambda m, p, n: next(answers))
    assert post(client, as_user("spd_a"), "key-0000006", SCHEDULE).status_code == 502
    assert post(client, as_user("spd_a"), "key-0000006", SCHEDULE).status_code == 201 and len(server.calls) == 2


def test_concurrent_duplicates_apply_once(ops, as_user, pg_engine, redis_client, upstream, committed, tokens):
    """Two requests with the same key at the same time (committed database, real concurrency)."""
    from database import Database
    gate = threading.Event()
    server = upstream(lambda m, p, n: (gate.wait(5) and False) or (201, {"id": str(uuid.uuid4())}, 0))
    app = create_app(make_settings(upstreams={"session": server.url, "catalog": "", "notifications": ""}),
                     database=Database(pg_engine), redis_client=redis_client)
    user = committed.users["spd_a"]
    headers = {"Authorization": f"Bearer {tokens.user(user.id, user.institution_id)}", "Idempotency-Key": "key-concurrent"}
    results = []

    def call():
        results.append(app.test_client().post(URL, json=SCHEDULE, headers=headers))
    first = threading.Thread(target=call)
    first.start()
    import time
    for _ in range(50):
        if server.calls:
            break
        time.sleep(0.05)
    duplicate = app.test_client().post(URL, json=SCHEDULE, headers=headers)
    gate.set()
    first.join(10)
    assert duplicate.status_code == 409 and duplicate.get_json()["error"] == "operation_in_progress"
    assert results[0].status_code == 201 and len(server.calls) == 1
    replay = app.test_client().post(URL, json=SCHEDULE, headers=headers)
    assert replay.status_code == 201 and replay.headers["Idempotent-Replayed"] == "true" and len(server.calls) == 1


@pytest.mark.parametrize("kind", ["count.final", "session.close", "vision.validation", "audit.delete", "model.activate", "x"])
def test_counting_and_history_operations_do_not_exist(ops, as_user, kind):
    client, server = ops(lambda m, p, n: (200, {}, 0))
    response = post(client, as_user("spd_a"), "key-0000007", {"type": kind, "payload": {}})
    assert response.status_code == 400 and response.get_json()["error"] == "unsupported_operation" and not server.calls


def test_validation_and_who_may_call(ops, as_user, as_client, machine):
    client, server = ops(lambda m, p, n: (200, {}, 0))
    assert post(client, as_user("spd_a"), "short", SCHEDULE).status_code == 400
    assert client.post(URL, json=SCHEDULE, headers=as_user("spd_a")).status_code == 400            # no key
    assert post(client, as_user("spd_a"), "key-0000008", {"type": "notification.read"}).status_code == 400  # no target
    assert client.post(URL, data="x", headers={**as_user("spd_a"), "Idempotency-Key": "key-0000009"}).status_code == 415
    machine("his", scopes=("sync.catalog.read",))
    assert post(client, as_client("his"), "key-0000010", SCHEDULE).status_code == 403             # clients: read only
    assert not server.calls


def test_notification_read_is_forwarded_as_patch(ops, as_user):
    target = uuid.uuid4()
    client, server = ops(lambda m, p, n: (200, {"id": str(target), "status": "read"}, 0))
    response = post(client, as_user("op_a"), "key-0000011", {"type": "notification.read", "target_id": str(target)})
    assert response.status_code == 200
    assert (server.calls[0]["method"], server.calls[0]["path"]) == ("PATCH", f"/api/v1/notifications/{target}/read")


def test_correlation_id_is_stable_per_principal_and_key(ops, as_user, world):
    client, server = ops(lambda m, p, n: (201, {"id": str(uuid.uuid4())}, 0))
    post(client, as_user("spd_a"), "key-0000012", SCHEDULE)
    expected = "op-" + hashlib.sha256(f"user:{world.users['spd_a'].id}:key-0000012".encode()).hexdigest()[:40]
    assert server.calls[0]["headers"]["X-Correlation-ID"] == expected
