"""MS-06 notifications: review pending, unresolved close, station alerts, duplicates, the e-mail channel.

Real PostgreSQL (rolled back). The e-mail channel is exercised against a local SMTP test server (a minimal
RFC 5321 receiver in a thread): no provider, no credential, no real e-mail.
"""

from __future__ import annotations

import socket
import socketserver
import threading
import uuid
from email import message_from_bytes

import pytest
from sqlalchemy import text

from services.email_channel import EmailSettings
from services.outbox_worker import OutboxWorker


def notifications(db_conn, *, session_id=None, kind=None, station=None):
    """station: only the alerts of that station (the shared test database keeps other runs' alerts, e.g. E2E)."""
    sql = "SELECT type, recipient_user_id, data, id FROM notification WHERE 1=1"
    params = {}
    if station is not None:
        sql += " AND data->>'station_id' = :st"
        params["st"] = str(station)
    if session_id is not None:
        sql += " AND session_id = :s"
        params["s"] = session_id
    if kind is not None:
        sql += " AND type = :t"
        params["t"] = kind
    return db_conn.execute(text(sql), params).all()


# --- session events -------------------------------------------------------------------------------------


def test_unsafe_close_is_notified_as_unresolved_not_as_closed(world, db_conn, worker):
    world.count("session_close", world.session, "spd_a2", {"closed_by": "spd_supervisor", "safe_close_confirmed": False,
                                                            "unreconciled_physical_shortages": [str(uuid.uuid4())]})
    worker.drain()
    found = [(t, r) for t, r, *_ in notifications(db_conn, session_id=world.session)]
    assert sorted(t for t, _ in found if t != "session_scheduled") == ["session_unresolved", "session_unresolved"]
    assert {r for t, r in found if t == "session_unresolved"} == {world.users["op_a"].id, world.users["spd_a"].id}


def test_safe_close_is_still_session_closed(world, db_conn, worker):
    world.count("session_close", world.session, "spd_a2", {"closed_by": "spd_supervisor", "safe_close_confirmed": True})
    worker.drain()
    assert {t for t, *_ in notifications(db_conn, session_id=world.session)} == {"session_scheduled", "session_closed"}


def test_no_duplicates_when_events_are_replayed(world, db_conn, worker):
    world.count("manual_close", world.session, "op_a", {"next_status": "awaiting_spd_review"})
    worker.drain()
    db_conn.execute(text("UPDATE notification_outbox SET processed_at = NULL WHERE session_id = :s"), {"s": world.session})
    worker.reconcile()
    worker.drain()
    assert len(notifications(db_conn, session_id=world.session, kind="review_pending")) == 1


# --- station alerts (MS-05 events) ---------------------------------------------------------------------------


@pytest.fixture
def station(world, db_conn):
    room, station_id, camera = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    db_conn.execute(text("INSERT INTO operating_room (id, code, name, institution_id) VALUES (:r, :c, 'Room', :i)"),
                    {"r": room, "c": f"OR-{world.tag}", "i": world.inst["A"]})
    db_conn.execute(text("INSERT INTO capture_station (id, name, room_id) VALUES (:s, 'Station 7', :r)"),
                    {"s": station_id, "r": room})
    db_conn.execute(text("INSERT INTO capture_camera (id, institution_id, station_id, name) VALUES (:c, :i, :s, 'Overhead')"),
                    {"c": camera, "i": world.inst["A"], "s": station_id})

    def event(kind, *, resolved=False, camera_id=None):
        event_id = uuid.uuid4()
        db_conn.execute(text("""INSERT INTO station_event (id, institution_id, station_id, camera_id, kind, detail, resolved_at)
                                VALUES (:id, :i, :s, :c, :k, '{"error": "NotReadableError"}'::jsonb,
                                        CASE WHEN :r THEN now() END)"""),
                        {"id": event_id, "i": world.inst["A"], "s": station_id, "c": camera_id, "k": kind, "r": resolved})
        return event_id
    return type("Station", (), {"id": station_id, "camera": camera, "event": staticmethod(event)})


def test_station_alert_is_captured_by_the_trigger_and_notified_to_supervisors_and_admins(world, db_conn, worker, station):
    event_id = station.event("camera_error", camera_id=station.camera)
    outbox = db_conn.execute(text("SELECT event_code, session_id FROM notification_outbox WHERE source_table = 'station_event' "
                                  "AND source_id = :e"), {"e": event_id}).all()
    assert outbox == [("station.camera_error", None)]
    worker.drain()
    found = notifications(db_conn, kind="station_alert", station=station.id)
    recipients = {r for _, r, *_ in found}
    assert recipients == {world.users["spd_a"].id, world.users["spd_a2"].id, world.users["admin_a"].id}
    assert world.users["op_b"].id not in recipients and world.users["off_spd_a"].id not in recipients
    data = found[0][2]
    assert (data["station_name"], data["kind"], data["camera_name"], data["error"]) == (
        "Station 7", "camera_error", "Overhead", "NotReadableError")


def test_recoveries_are_not_notified(world, db_conn, worker, station):
    station.event("camera_recovered")
    station.event("heartbeat_recovered")
    worker.drain()
    assert notifications(db_conn, kind="station_alert", station=station.id) == []


def test_one_unread_alert_per_station_and_kind(world, db_conn, worker, station):
    station.event("heartbeat_lost", resolved=True)
    station.event("heartbeat_lost")            # it came back and was lost again, before anyone read the first
    worker.drain()
    assert len([r for _, r, *_ in notifications(db_conn, kind="station_alert", station=station.id) if r == world.users["spd_a"].id]) == 1
    db_conn.execute(text("UPDATE notification SET status = 'read', read_at = now() WHERE type = 'station_alert'"))
    station.event("camera_error")
    worker.drain()
    kinds = sorted(d["kind"] for _, r, d, _ in notifications(db_conn, kind="station_alert", station=station.id) if r == world.users["spd_a"].id)
    assert kinds == ["camera_error", "heartbeat_lost"]


def test_station_alerts_through_the_api(client, world, db_conn, worker, station, as_user):
    station.event("heartbeat_lost")
    worker.drain()
    page = client.get("/api/v1/notifications", headers=as_user("spd_a")).get_json()
    alert = next(i for i in page["items"] if i["type"] == "station_alert")
    assert alert["title"] == "Capture station alert" and "Station 7 stopped reporting" in alert["message"]
    assert alert["session_id"] is None and alert["data"]["station_name"] == "Station 7"
    assert client.patch(f"/api/v1/notifications/{alert['id']}/read", headers=as_user("spd_a")).status_code == 200
    assert client.get(f"/api/v1/notifications/{alert['id']}", headers=as_user("op_b")).status_code == 404
    xml = client.get("/api/v1/notifications", headers=as_user("spd_a", Accept="application/xml"))
    assert xml.status_code == 200 and b"station_alert" in xml.data


# --- e-mail channel -------------------------------------------------------------------------------------


class _SMTPHandler(socketserver.StreamRequestHandler):
    def handle(self):
        self.wfile.write(b"220 test-smtp ready\r\n")
        data_mode, lines = False, []
        while True:
            line = self.rfile.readline()
            if not line:
                return
            if data_mode:
                if line in (b".\r\n", b".\n"):
                    self.server.messages.append(message_from_bytes(b"".join(lines)))
                    data_mode, lines = False, []
                    self.wfile.write(b"250 queued\r\n")
                else:
                    lines.append(line[1:] if line.startswith(b"..") else line)
                continue
            command = line.strip().upper()
            if command.startswith((b"EHLO", b"HELO")):
                self.wfile.write(b"250 test-smtp\r\n")
            elif command.startswith((b"MAIL", b"RCPT", b"RSET", b"NOOP")):
                self.wfile.write(b"250 ok\r\n")
            elif command == b"DATA":
                data_mode = True
                self.wfile.write(b"354 end with .\r\n")
            elif command == b"QUIT":
                self.wfile.write(b"221 bye\r\n")
                return
            else:
                self.wfile.write(b"502 not implemented\r\n")


@pytest.fixture
def quiet_outbox(db_conn):
    """Only this test's events in the outbox (older pending rows of the shared test database are set aside,
    inside the rolled-back transaction)."""
    db_conn.execute(text("UPDATE notification_outbox SET processed_at = now() WHERE processed_at IS NULL"))
    db_conn.execute(text("UPDATE notification_delivery SET status = 'sent' WHERE status = 'pending'"))


@pytest.fixture
def smtp_server():
    server = socketserver.ThreadingTCPServer(("127.0.0.1", 0), _SMTPHandler)
    server.daemon_threads = True
    server.messages = []
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield server
    server.shutdown()
    server.server_close()


def _email(port, **overrides):
    values = dict(host="127.0.0.1", port=port, sender="pef@hospital.test", security="none", timeout=3,
                  types=("discrepancy_pending", "review_pending", "session_unresolved", "station_alert"),
                  portal_url="https://pef.hospital.test")
    values.update(overrides)
    return EmailSettings(**values)


def _deliveries(db_conn, institution):
    """Deliveries of this test's institution (the worker also drains other pending rows of the test database)."""
    return db_conn.execute(text("SELECT n.type, d.status, d.attempts, d.last_error FROM notification_delivery d "
                                "JOIN notification n ON n.id = d.notification_id WHERE n.institution_id = :i "
                                "ORDER BY d.id"), {"i": institution}).all()


def test_email_channel_sends_configured_types_without_clinical_data(world, db_conn, database, quiet_outbox, station,
                                                                     smtp_server):
    worker = OutboxWorker(database, email=_email(smtp_server.server_address[1]))
    world.count("manual_close", world.session, "op_a", {"next_status": "awaiting_spd_review"})
    station.event("camera_error", camera_id=station.camera)
    worker.drain()
    pending = _deliveries(db_conn, world.inst['A'])
    # session_scheduled is not an e-mail type: only review_pending (1) and station_alert (3 recipients).
    assert sorted(t for t, *_ in pending) == ["review_pending", "station_alert", "station_alert", "station_alert"]
    worker.deliver_emails(limit=500)
    assert {s for _, s, *_ in _deliveries(db_conn, world.inst['A'])} == {"sent"}
    ours = [m for m in smtp_server.messages if m["To"].endswith(f"{world.tag}@notif-test.invalid")]
    subjects = sorted(m["Subject"] for m in ours)
    assert subjects == ["[PEF] Capture station alert"] * 3 + ["[PEF] Session pending review"]
    body = next(m for m in ours if "pending review" in m["Subject"]).get_payload()
    assert "WS-" in body and "https://pef.hospital.test/notifications" in body
    assert "password" not in body.lower() and "token" not in body.lower()
    assert worker.deliver_emails() == {"sent": 0, "failed": 0}          # nothing is sent twice
    assert len(ours) == 4


def test_mail_server_down_never_affects_the_in_app_notification(world, db_conn, database, quiet_outbox, station):
    closed = socket.socket()
    closed.bind(("127.0.0.1", 0))
    port = closed.getsockname()[1]
    closed.close()
    worker = OutboxWorker(database, email=_email(port, timeout=1), max_attempts=2)
    station.event("heartbeat_lost")
    worker.drain()
    assert len(notifications(db_conn, kind="station_alert", station=station.id)) == 3        # in-app notifications committed
    assert worker.deliver_emails()["failed"] == 1
    status, attempts, error = _deliveries(db_conn, world.inst['A'])[0][1:]
    assert (status, attempts) == ("pending", 1) and error
    worker.deliver_emails()
    assert _deliveries(db_conn, world.inst['A'])[0][1:3] == ("failed", 2)                  # gave up after max_attempts
    assert len(notifications(db_conn, kind="station_alert", station=station.id)) == 3


def test_email_channel_disabled_without_configuration(world, db_conn, database, station):
    worker = OutboxWorker(database)                                         # no NOTIFICATIONS_SMTP_HOST
    station.event("camera_error")
    worker.drain()
    assert notifications(db_conn, kind="station_alert", station=station.id) and _deliveries(db_conn, world.inst['A']) == []
    assert worker.deliver_emails() == {"sent": 0, "failed": 0}
