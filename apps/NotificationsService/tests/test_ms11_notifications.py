"""MS-11 (R06): escalation of a possible physical difference and platform availability alerts.

Real PostgreSQL (rolled back), real triggers of 017 and the real outbox worker.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import text

from services.notification_service import item


def _notifications(db_conn, kind, *, session_id=None, component=None):
    sql = "SELECT recipient_user_id, data FROM notification WHERE type = :t"
    params = {"t": kind}
    if session_id is not None:
        sql += " AND session_id = :s"
        params["s"] = session_id
    if component is not None:
        sql += " AND data->>'component' = :c"
        params["c"] = component
    return db_conn.execute(text(sql), params).all()


def _service_event(db_conn, component, kind, status):
    event_id = uuid.uuid4()
    db_conn.execute(text("""INSERT INTO service_event (id, component, kind, status, error_type, occurred_at)
                            VALUES (:id, :c, :k, :s, 'connection_error', :t)"""),
                    {"id": event_id, "c": component, "k": kind, "s": status, "t": datetime.now(timezone.utc)})
    return event_id


def test_final_count_escalation_notifies_the_scheduling_supervisor(world, db_conn, worker):
    world.audit("session.final_count_escalated", world.session, "op_a")
    worker.drain()
    found = _notifications(db_conn, "final_count_escalated", session_id=world.session)
    assert {r for r, _ in found} == {world.users["spd_a"].id}           # the supervisor who scheduled it
    assert found[0][1]["session_label"].startswith("WS-")
    assert world.users["op_a"].id not in {r for r, _ in found}          # never the actor of the report


def test_escalation_of_another_institution_is_never_notified_to_this_one(world, db_conn, worker):
    session_b = world.new_session(inst="B", operator="op_b")
    world.audit("session.final_count_escalated", session_b, "op_b", inst="B")
    worker.drain()
    recipients = {r for r, _ in _notifications(db_conn, "final_count_escalated", session_id=session_b)}
    assert not recipients & {u.id for key, u in world.users.items() if key.endswith("_a") or key.endswith("_a2")}


def test_service_down_and_recovered_reach_it_administrators_only(world, db_conn, worker):
    component = f"catalog-{world.tag}"
    event_id = _service_event(db_conn, component, "down", "down")
    outbox = db_conn.execute(text("SELECT event_code FROM notification_outbox WHERE source_table = 'service_event' "
                                  "AND source_id = :e"), {"e": event_id}).scalars().all()
    assert outbox == ["service.down"]                                   # captured by the 017 trigger
    worker.drain()
    down = _notifications(db_conn, "service_alert", component=component)
    recipients = {r for r, _ in down}
    assert world.users["admin_a"].id in recipients
    assert not recipients & {world.users[k].id for k in ("spd_a", "spd_a2", "op_a", "op_b")}
    data = next(d for r, d in down if r == world.users["admin_a"].id)
    assert (data["component"], data["kind"], data["status"]) == (component, "down", "down")
    _service_event(db_conn, component, "recovered", "up")
    worker.drain()
    kinds = {d["kind"] for r, d in _notifications(db_conn, "service_alert", component=component) if r == world.users["admin_a"].id}
    assert kinds == {"down", "recovered"}


def test_one_unread_alert_per_component_and_kind(world, db_conn, worker):
    component = f"session-{world.tag}"
    _service_event(db_conn, component, "down", "down")
    _service_event(db_conn, component, "down", "down")
    worker.drain()
    mine = [d for r, d in _notifications(db_conn, "service_alert", component=component) if r == world.users["admin_a"].id]
    assert len(mine) == 1


def test_api_representation_of_the_new_types():
    base = {"id": uuid.uuid4(), "status": "unread", "session_id": None, "created_at": datetime.now(timezone.utc),
            "read_at": None}
    service = item({**base, "type": "service_alert", "data": {"component": "evidence", "kind": "down", "status": "down"}})
    assert service["title"] == "Platform component alert"
    assert "evidence is not available" in service["message"] and "technical availability" in service["message"]
    assert service["data"] == {"component": "evidence", "kind": "down", "status": "down"}
    escalation = item({**base, "type": "final_count_escalated", "session_id": uuid.uuid4(),
                       "data": {"session_label": "WS-ABCDEF12"}})
    assert "WS-ABCDEF12" in escalation["message"] and "no valid final count" in escalation["message"]


def test_startup_migration_does_not_deadlock_with_a_concurrent_trigger_write(pg_engine):
    """MS-11 regression (observed): the migration re-applied at start-up (006 -> 013 -> 017, one transaction) locked
    notification_outbox and then needed service_event, while a running writer held service_event and waited for
    notification_outbox (its trigger) -> deadlock, the service did not start. The migration now locks the source tables
    first; this test forces the same interleaving deterministically."""
    import threading
    import time as _time

    from database import apply_migrations

    errors = []
    writer = pg_engine.connect()
    tx = writer.begin()
    writer.execute(text("LOCK TABLE service_event IN ROW EXCLUSIVE MODE"))     # like an INSERT INTO service_event

    def migrate():
        try:
            apply_migrations(pg_engine)
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)
    thread = threading.Thread(target=migrate)
    thread.start()
    _time.sleep(1.5)                                     # the migration is now waiting (or, before the fix, holding the outbox)
    try:
        writer.execute(text("LOCK TABLE notification_outbox IN ROW EXCLUSIVE MODE"))   # what the trigger needs next
        tx.commit()
    except Exception as exc:  # noqa: BLE001
        tx.rollback()
        errors.append(exc)
    finally:
        writer.close()
    thread.join(timeout=60)
    assert not thread.is_alive()
    assert errors == [], [type(e).__name__ for e in errors]
