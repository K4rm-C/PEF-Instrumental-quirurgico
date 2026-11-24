"""ELECTRON-01: observe-only monitor over /ws (live_monitor.py + monitor_ticket.py).

Real WebSocket server (werkzeug + flask-sock in a thread) and real client (simple_websocket). The session state
is a plain object: these tests never load YOLO/torch and never touch the Operator's capture path.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import threading
import time
import uuid
from types import SimpleNamespace

import pytest
import simple_websocket
from flask import Flask
from flask_sock import Sock
from werkzeug.serving import make_server

import live_monitor
from monitor_ticket import monitor_ticket_expiry

SECRET = "pytest-monitor-secret"
SID = str(uuid.uuid4())


def monitor_ticket(session_id=SID, ttl=60, secret=SECRET):
    exp, nonce = int(time.time()) + ttl, uuid.uuid4().hex
    sig = hmac.new(secret.encode(), f"pef-monitor:v1:{session_id}:{exp}:{nonce}".encode(), hashlib.sha256).hexdigest()
    return f"m1.{session_id}.{exp}.{nonce}.{sig}"


def portal_ticket(session_id=SID, ttl=60, secret=SECRET):
    exp, nonce = int(time.time()) + ttl, uuid.uuid4().hex
    sig = hmac.new(secret.encode(), f"{session_id}:{exp}:{nonce}".encode(), hashlib.sha256).hexdigest()
    return f"{session_id}.{exp}.{nonce}.{sig}"


# --- ticket -------------------------------------------------------------------------------------------------

def test_monitor_ticket_rules():
    assert monitor_ticket_expiry(monitor_ticket(), session_id=SID, secret=SECRET) is not None
    assert monitor_ticket_expiry(monitor_ticket(), session_id=str(uuid.uuid4()), secret=SECRET) is None  # other session
    assert monitor_ticket_expiry(monitor_ticket(ttl=-5), session_id=SID, secret=SECRET) is None          # expired
    assert monitor_ticket_expiry(monitor_ticket(secret="x"), session_id=SID, secret=SECRET) is None      # forged
    assert monitor_ticket_expiry(portal_ticket(), session_id=SID, secret=SECRET) is None                 # portal ticket
    assert monitor_ticket_expiry(monitor_ticket(), session_id=SID, secret="") is None                    # disabled
    tampered = monitor_ticket().replace("m1.", "m2.", 1)
    assert monitor_ticket_expiry(tampered, session_id=SID, secret=SECRET) is None


# --- real server --------------------------------------------------------------------------------------------

class World:
    def __init__(self):
        self.states: dict[str, object] = {}
        self.calls: list[str] = []

    def get_state(self, sid):
        self.calls.append(sid)
        return self.states.get(sid)


def fake_state(frames=1, status="processing", source="live"):
    return SimpleNamespace(
        status=status, capture_source=source, message="live camera", run_id="run1", frames_processed=frames,
        model_version="m-test", last_frame_at=time.time(), progress=0.0, frame_jpeg_b64="QUJD",
        counts={"fam_a": {"family_code": "fam_a", "family_id": "f-a", "detected_quantity": 2}},
        boxes=[{"xyxy": [10, 10, 40, 60], "family_code": "fam_a", "family_name": "Fam A", "confidence": 0.91,
                "high_confidence": True, "held": False}],
    )


@pytest.fixture
def server(monkeypatch):
    monkeypatch.setattr(live_monitor, "MONITOR_MIN_INTERVAL_S", 0.1)
    monkeypatch.setattr(live_monitor, "MONITOR_HEARTBEAT_S", 0.5)
    monkeypatch.setattr(live_monitor, "MONITOR_MAX_OBSERVERS", 2)
    monkeypatch.setattr(live_monitor, "_observers", {})
    world = World()
    app = Flask(__name__)
    sock = Sock(app)

    @sock.route("/ws")
    def ws_route(ws):  # mirrors live_ws: first message decides the mode
        first = json.loads(ws.receive())
        if first.get("type") == "monitor.subscribe":
            live_monitor.run_observer(ws, first, get_state=world.get_state, secret=world.secret)
            return
        ws.send(json.dumps({"type": "error", "code": "unexpected"}))

    world.secret = SECRET
    http = make_server("127.0.0.1", 0, app, threaded=True)
    thread = threading.Thread(target=http.serve_forever, daemon=True)
    thread.start()
    world.url = f"ws://127.0.0.1:{http.server_port}/ws"
    yield world
    http.shutdown()


def recv(ws, timeout=3.0):
    raw = ws.receive(timeout=timeout)
    return None if raw is None else json.loads(raw)


def recv_until(ws, predicate, timeout=4.0):
    end = time.time() + timeout
    while time.time() < end:
        msg = recv(ws, timeout=max(0.05, end - time.time()))
        if msg is not None and predicate(msg):
            return msg
    return None


def close(ws):
    try:
        ws.close()
    except simple_websocket.ConnectionClosed:
        pass   # the worker already closed it (refusal / expiry): that is the behaviour under test


def subscribe(server, ticket=None, session_id=SID):
    ws = simple_websocket.Client.connect(server.url)
    ws.send(json.dumps({"type": "monitor.subscribe", "session_id": session_id,
                        "auth_token": ticket or monitor_ticket(session_id)}))
    return ws


def test_invalid_and_portal_tickets_are_refused(server):
    for bad in ("garbage", portal_ticket(), monitor_ticket(secret="other"), monitor_ticket(ttl=-1)):
        ws = subscribe(server, ticket=bad)
        msg = recv(ws)
        assert msg["type"] == "error" and msg["code"] == "unauthorized"
        close(ws)
    assert server.calls == []   # the session state was never read


def test_disabled_without_secret(server):
    server.secret = ""
    ws = subscribe(server)
    assert recv(ws)["code"] == "monitor_disabled"
    close(ws)


def test_observer_receives_state_and_only_new_frames(server):
    server.states[SID] = fake_state(frames=1)
    ws = subscribe(server)
    assert recv(ws)["type"] == "monitor.subscribed"
    first = recv_until(ws, lambda m: m["type"] == "monitor.state")
    assert first["frame"]["jpeg_b64"] == "QUJD"
    assert first["worker"]["frames_processed"] == 1
    assert first["counts_by_family"] == [{"family_code": "fam_a", "family_id": "f-a", "detected_quantity": 2}]
    assert first["detections"][0]["bbox"] == [10.0, 10.0, 30.0, 50.0]
    assert first["worker"]["frame_age_s"] is not None
    # No new inference: only frameless heartbeats.
    beat = recv_until(ws, lambda m: m["type"] == "monitor.state")
    assert beat["frame"] is None
    # New inference: a new frame.
    server.states[SID].frames_processed = 2
    fresh = recv_until(ws, lambda m: m["type"] == "monitor.state" and m["frame"] is not None)
    assert fresh["frame"]["frames_processed"] == 2
    close(ws)


def test_unbound_session_is_reported_not_invented(server):
    ws = subscribe(server)
    recv(ws)
    msg = recv_until(ws, lambda m: m["type"] == "monitor.state")
    assert msg["worker"]["status"] == "unbound"
    assert msg["counts_by_family"] == [] and msg["frame"] is None
    close(ws)


def test_observer_cannot_submit_frames_or_cancel(server):
    state = fake_state(frames=5)
    server.states[SID] = state
    ws = subscribe(server)
    recv(ws)
    for forbidden in ({"type": "frame.submit", "image_base64": "QUJD"}, {"type": "session.cancel"},
                      {"type": "session.bind", "session_id": SID}):
        ws.send(json.dumps(forbidden))
        err = recv_until(ws, lambda m: m["type"] == "error")
        assert err["code"] == "forbidden"
    assert state.frames_processed == 5 and state.status == "processing"
    close(ws)


def test_expiry_without_renewal_closes(server):
    server.states[SID] = fake_state()
    ws = subscribe(server, ticket=monitor_ticket(ttl=1))
    recv(ws)
    err = recv_until(ws, lambda m: m["type"] == "error", timeout=5)
    assert err["code"] == "session_expired"
    close(ws)


def test_renewal_keeps_the_stream_and_foreign_renewal_closes(server):
    server.states[SID] = fake_state()
    ws = subscribe(server, ticket=monitor_ticket(ttl=1))
    recv(ws)
    ws.send(json.dumps({"type": "monitor.renew", "auth_token": monitor_ticket(ttl=60)}))
    assert recv_until(ws, lambda m: m["type"] == "monitor.renewed") is not None
    time.sleep(1.5)
    assert recv_until(ws, lambda m: m["type"] == "monitor.state") is not None   # still alive after the 1 s ticket
    ws.send(json.dumps({"type": "monitor.renew", "auth_token": monitor_ticket(session_id=str(uuid.uuid4()))}))
    err = recv_until(ws, lambda m: m["type"] == "error")
    assert err["code"] == "unauthorized"
    close(ws)


def test_observer_limit_per_session(server):
    server.states[SID] = fake_state()
    a, b = subscribe(server), subscribe(server)
    assert recv(a)["type"] == "monitor.subscribed" and recv(b)["type"] == "monitor.subscribed"
    c = subscribe(server)
    assert recv_until(c, lambda m: m["type"] == "error")["code"] == "busy"
    for ws in (a, b, c):
        close(ws)


def test_worker_restart_shows_as_unbound_then_recovers(server):
    server.states[SID] = fake_state(frames=3)
    ws = subscribe(server)
    recv(ws)
    assert recv_until(ws, lambda m: m["type"] == "monitor.state")["worker"]["status"] == "processing"
    server.states.clear()          # worker forgot the session (restart)
    assert recv_until(ws, lambda m: m["type"] == "monitor.state" and m["worker"]["status"] == "unbound")
    server.states[SID] = fake_state(frames=1)   # portal re-bound and capture resumed
    assert recv_until(ws, lambda m: m["type"] == "monitor.state" and m["frame"] is not None)
    close(ws)


def test_live_ws_delegates_monitor_subscribe():
    """The production /ws handler routes monitor.subscribe to the observer (needs the inference stack)."""
    pytest.importorskip("torch")
    import inspect

    import live_ws
    source = inspect.getsource(live_ws.register_live_ws)
    assert 'mtype == "monitor.subscribe" and bound is None' in source
    assert "run_observer(ws, msg, get_state=get_state)" in source
