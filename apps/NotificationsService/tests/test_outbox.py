"""Capture (triggers), rules, idempotency, reconciliation and failure handling of the outbox."""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import text

from services import outbox_worker


def outbox(db_conn, source_id):
    return db_conn.execute(text("SELECT event_code, processed_at IS NOT NULL, attempts FROM notification_outbox "
                                "WHERE source_id = :id"), {"id": source_id}).all()


def notifications(db_conn, session_id):
    return sorted((row[0], row[1]) for row in db_conn.execute(text(
        "SELECT type, recipient_user_id FROM notification WHERE session_id = :s"), {"s": session_id}).all())


def test_migration_is_idempotent_and_shipped_with_the_image(pg_engine):
    from database import apply_migrations
    apply_migrations(pg_engine)
    apply_migrations(pg_engine)
    service_dir = Path(__file__).resolve().parents[1]
    assert (service_dir / "migrations" / "006_notifications.sql").read_bytes() == \
        (service_dir.parents[1] / "data" / "migrations" / "006_notifications.sql").read_bytes()


def test_triggers_capture_only_relevant_events_in_the_producer_transaction(world, db_conn):
    assert outbox(db_conn, world.scheduled) == [("session.scheduled", False, 0)]
    other = world.audit("session.inventory_updated", world.session, "spd_a")
    auto = world.count("auto_count", world.session, payload={"trigger": "initial"})
    close = world.count("manual_close", world.session, "op_a", {"next_status": "awaiting_spd_review"})
    assert outbox(db_conn, other) == [] and outbox(db_conn, auto) == []
    assert outbox(db_conn, close) == [("manual_close", False, 0)]


def test_session_scheduled_notifies_the_assigned_operator(world, db_conn, worker):
    stats = worker.drain()
    assert stats["notifications"] >= 1
    assert notifications(db_conn, world.session) == [("session_scheduled", world.users["op_a"].id)]
    data = db_conn.execute(text("SELECT data FROM notification WHERE session_id = :s"), {"s": world.session}).scalar_one()
    assert data == {"session_label": "WS-" + str(world.session).replace("-", "")[:8].upper(),
                    "scheduled_at": "2026-11-03T09:30:00Z"}
    assert outbox(db_conn, world.scheduled) == [("session.scheduled", True, 0)]


def test_report_with_discrepancies_notifies_scheduler_and_operator(world, db_conn, worker):
    world.count("manual_close", world.session, "op_a", {"next_status": "correction_required", "open_discrepancies": 2})
    worker.drain()
    assert notifications(db_conn, world.session) == sorted([
        ("session_scheduled", world.users["op_a"].id),
        ("discrepancy_pending", world.users["spd_a"].id),       # the supervisor who scheduled it, not spd_a2
        ("correction_required", world.users["op_a"].id)])
    data = db_conn.execute(text("SELECT data FROM notification WHERE type = 'discrepancy_pending' AND session_id = :s"),
                           {"s": world.session}).scalar_one()
    assert data["open_discrepancies"] == 2


def test_report_without_discrepancies_notifies_nobody(world, db_conn, worker):
    world.count("manual_close", world.session, "op_a", {"next_status": "awaiting_spd_review"})
    worker.drain()
    assert [t for t, _ in notifications(db_conn, world.session)] == ["session_scheduled"]


def test_unknown_scheduler_falls_back_to_active_supervisors_of_the_institution(world, db_conn, worker):
    legacy = world.new_session()   # no session.scheduled audit row (legacy / seeded data)
    world.count("manual_close", legacy, "op_a", {"next_status": "correction_required", "open_discrepancies": 1})
    worker.drain()
    recipients = {r for t, r in notifications(db_conn, legacy) if t == "discrepancy_pending"}
    assert recipients == {world.users["spd_a"].id, world.users["spd_a2"].id}   # off_spd_a is inactive


def test_resolution_and_close_notify_the_related_users_but_not_the_actor(world, db_conn, worker):
    world.audit("discrepancy.resolve", world.session, "spd_a2")
    world.count("session_close", world.session, "spd_a", {"closed_by": "spd_supervisor"})
    worker.drain()
    got = notifications(db_conn, world.session)
    assert ("discrepancy_resolved", world.users["op_a"].id) in got
    closed = {r for t, r in got if t == "session_closed"}
    assert closed == {world.users["op_a"].id}   # spd_a scheduled it but closed it himself


def test_replay_and_double_capture_never_duplicate(world, db_conn, worker):
    worker.drain()
    first = notifications(db_conn, world.session)
    db_conn.execute(text("UPDATE notification_outbox SET processed_at = NULL WHERE source_id = :id"), {"id": world.scheduled})
    worker.drain()
    worker.reconcile()
    worker.drain()
    assert notifications(db_conn, world.session) == first
    assert db_conn.execute(text("SELECT count(*) FROM notification_outbox WHERE source_id = :id"),
                           {"id": world.scheduled}).scalar_one() == 1


def test_reconciliation_recovers_an_event_the_trigger_missed(world, db_conn, worker):
    db_conn.execute(text("DELETE FROM notification_outbox WHERE source_id = :id"), {"id": world.scheduled})
    assert worker.reconcile() >= 1
    worker.drain()
    assert notifications(db_conn, world.session) == [("session_scheduled", world.users["op_a"].id)]


def test_a_failing_event_is_retried_and_does_not_block_the_others(world, db_conn, worker, monkeypatch):
    other = world.new_session()
    second = world.audit("session.scheduled", other, "spd_a")
    real_plan = outbox_worker.dispatcher.plan

    def flaky(connection, event):
        if event["source_id"] == world.scheduled:
            raise RuntimeError("boom")
        return real_plan(connection, event)
    monkeypatch.setattr(outbox_worker.dispatcher, "plan", flaky)
    worker.drain()
    assert outbox(db_conn, world.scheduled)[0][1:] == (False, 1)
    assert outbox(db_conn, second)[0][1] is True
    monkeypatch.setattr(outbox_worker.dispatcher, "plan", real_plan)
    worker.drain()
    assert outbox(db_conn, world.scheduled)[0][1] is True
    assert notifications(db_conn, world.session) == [("session_scheduled", world.users["op_a"].id)]


def test_poison_event_stops_after_max_attempts(world, db_conn, database, monkeypatch):
    worker = outbox_worker.OutboxWorker(database, max_attempts=2)
    monkeypatch.setattr(outbox_worker.dispatcher, "plan", lambda connection, event: (_ for _ in ()).throw(ValueError("bad")))
    worker.drain()
    worker.drain()
    row = db_conn.execute(text("SELECT attempts, processed_at, last_error FROM notification_outbox WHERE source_id = :id"),
                          {"id": world.scheduled}).one()
    assert row[0] == 2 and row[1] is None and row[2].startswith("ValueError")


def test_events_of_removed_sessions_are_consumed_without_notifications(world, db_conn, worker):
    gone = world.audit("session.scheduled", world.new_session(), "spd_a")
    db_conn.execute(text("UPDATE notification_outbox SET session_id = gen_random_uuid() WHERE source_id = :id"), {"id": gone})
    worker.drain()
    assert outbox(db_conn, gone)[0][1] is True


def test_several_resolutions_in_a_row_are_one_unread_notice(world, db_conn, worker):
    for supervisor in ("spd_a", "spd_a2", "spd_a"):
        world.audit("discrepancy.resolve", world.session, supervisor)
    worker.drain()
    assert [t for t, _ in notifications(db_conn, world.session)].count("discrepancy_resolved") == 1
    db_conn.execute(text("UPDATE notification SET status = 'read', read_at = now() WHERE type = 'discrepancy_resolved' "
                         "AND session_id = :s"), {"s": world.session})
    world.audit("discrepancy.resolve", world.session, "spd_a")      # a later resolution, once the first was read
    worker.drain()
    assert [t for t, _ in notifications(db_conn, world.session)].count("discrepancy_resolved") == 2
