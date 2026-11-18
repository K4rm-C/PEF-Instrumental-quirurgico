"""MS-03: a live WebSocket keeps inferring only while its user is authorized — enforced by the worker.

Real server (werkzeug in a thread) and real WebSocket client (simple_websocket, the library flask-sock uses);
YOLO is faked (no torch work), the model pin is faked as in test_edge_frames.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import threading
import time
import uuid

import cv2
import numpy as np
import pytest
import simple_websocket
from werkzeug.serving import make_server

import bind_auth
import inference
import live_ws
import main
from config import WS_SECRET
from ws_ticket import ticket_claims


def ticket(session_id, user_id, ttl=60, secret=WS_SECRET):
    exp, nonce = int(time.time()) + ttl, uuid.uuid4().hex
    sig = hmac.new(secret.encode(), f"{session_id}:{user_id}:{exp}:{nonce}".encode(), hashlib.sha256).hexdigest()
    return f"{session_id}.{user_id}.{exp}.{nonce}.{sig}"


# --- ticket claims ------------------------------------------------------------------------------------

def test_ticket_claims():
    assert ticket_claims(ticket("s1", "u1"), session_id="s1", secret=WS_SECRET)["user_id"] == "u1"
    assert ticket_claims(ticket("s1", "u1"), session_id="s2", secret=WS_SECRET) is None          # other session
    assert ticket_claims(ticket("s1", "u1", ttl=-5), session_id="s1", secret=WS_SECRET) is None  # expired
    assert ticket_claims(ticket("s1", "u1", secret="other"), session_id="s1", secret=WS_SECRET) is None
    exp, nonce = int(time.time()) + 60, "n"
    old = f"s1.{exp}.{nonce}." + hmac.new(WS_SECRET.encode(), f"s1:{exp}:{nonce}".encode(), hashlib.sha256).hexdigest()
    assert ticket_claims(old, session_id="s1", secret=WS_SECRET) is None                          # pre-MS-03 format


# --- real server ----------------------------------------------------------------------------------------

@pytest.fixture
def server(monkeypatch):
    monkeypatch.setattr(main, "_sessions", {})
    monkeypatch.setattr(inference, "get_model", lambda *_: object())
    monkeypatch.setattr(inference, "detect_with_class_scores",
                        lambda _m, frame: [{"xyxy": [10.0, 10.0, 40.0, 60.0], "class_id": 0, "confidence": 0.9,
                                            "scores_by_class": [0.9], "topk": [{"class_id": 0, "score": 0.9}]}])
    monkeypatch.setattr(main.MODEL_CACHE, "prepare", lambda spec, ids=(): {
        "model_id": spec.model_id, "model_version": spec.model_version, "sha256": spec.sha256, "source": "test"})
    monkeypatch.setattr(live_ws, "_origin_ok", lambda: True)
    http = make_server("127.0.0.1", 0, main.app, threaded=True)
    thread = threading.Thread(target=http.serve_forever, daemon=True)
    thread.start()
    base = f"127.0.0.1:{http.server_port}"
    client = main.app.test_client()
    body = json.dumps({"expected": [{"family_code": "A", "family_id": "f-a", "expected_quantity": 1}],
                       "class_map": [{"yolo_class_id": 0, "family_code": "A", "family_id": "f-a"}],
                       "model": {"model_id": "m", "model_version": "v", "sha256": "ab" * 32}}).encode()
    assert client.post("/sessions/s1/bind", data=body, content_type="application/json",
                       headers={"X-PEF-Signature": bind_auth.sign(body)}).status_code == 200
    yield base, client
    http.shutdown()


FRAME = base64.b64encode(cv2.imencode(".jpg", np.full((120, 160, 3), 120, np.uint8))[1].tobytes()).decode()


def connect(base, token, session_id="s1"):
    ws = simple_websocket.Client(f"ws://{base}/ws")
    ws.send(json.dumps({"type": "session.bind", "session_id": session_id, "auth_token": token}))
    return ws


def messages(ws, until, timeout=8.0):
    seen, deadline = [], time.time() + timeout
    while time.time() < deadline:
        try:
            raw = ws.receive(timeout=0.5)
        except simple_websocket.ConnectionClosed:
            seen.append({"type": "closed"})
            break
        if raw is None:
            continue
        seen.append(json.loads(raw))
        if until(seen[-1]):
            break
    return seen


def frame(ws, frame_id="f1"):
    ws.send(json.dumps({"type": "frame.submit", "frame_id": frame_id, "image_base64": FRAME}))


def test_valid_connection_infers(server):
    base, _ = server
    ws = connect(base, ticket("s1", "u1"))
    assert any(m.get("state") == "ready" for m in messages(ws, lambda m: m.get("state") == "ready"))
    frame(ws)
    got = messages(ws, lambda m: m.get("type") == "detections.result")
    assert got[-1]["type"] == "detections.result" and got[-1]["model_sha256"] == "ab" * 32
    ws.close()


def test_revocation_closes_an_open_connection_and_stops_results(server):
    base, client = server
    ws = connect(base, ticket("s1", "u-revoked"))
    messages(ws, lambda m: m.get("state") == "ready")
    unsigned = client.post("/auth/revoke", json={"user_id": "u-revoked"})
    assert unsigned.status_code == 401                                   # only the portal may revoke
    raw = json.dumps({"user_id": "u-revoked"}).encode()
    revoked = client.post("/auth/revoke", data=raw, content_type="application/json",
                          headers={"X-PEF-Signature": bind_auth.sign(raw)})
    assert revoked.get_json()["revoked_connections"] == 1
    got = messages(ws, lambda m: m.get("type") in ("error", "closed"))
    assert any(m.get("code") == "revoked" for m in got)
    try:
        frame(ws, "after-revocation")
        after = messages(ws, lambda m: m.get("type") == "detections.result", timeout=2.5)
    except simple_websocket.ConnectionClosed:
        after = [{"type": "closed"}]
    assert not any(m.get("type") == "detections.result" for m in after)
    assert client.get("/health").get_json()["live_connections"] == 0


def test_revocation_of_one_user_keeps_other_users(server):
    base, client = server
    other = connect(base, ticket("s1", "u-other"))
    messages(other, lambda m: m.get("state") == "ready")
    victim = connect(base, ticket("s1", "u-victim"))
    messages(victim, lambda m: m.get("state") == "ready")
    raw = json.dumps({"user_id": "u-victim"}).encode()
    client.post("/auth/revoke", data=raw, content_type="application/json", headers={"X-PEF-Signature": bind_auth.sign(raw)})
    assert any(m.get("code") == "revoked" for m in messages(victim, lambda m: m.get("type") in ("error", "closed")))
    frame(other)
    assert messages(other, lambda m: m.get("type") == "detections.result")[-1]["type"] == "detections.result"
    other.close()


def test_expired_ticket_closes_the_connection_unless_renewed(server):
    base, _ = server
    ws = connect(base, ticket("s1", "u1", ttl=2))
    messages(ws, lambda m: m.get("state") == "ready")
    got = messages(ws, lambda m: m.get("type") in ("error", "closed"), timeout=6)
    assert any(m.get("code") == "session_expired" for m in got)

    renewed = connect(base, ticket("s1", "u1", ttl=2))                   # reconnection with a fresh ticket
    messages(renewed, lambda m: m.get("state") == "ready")
    renewed.send(json.dumps({"type": "session.renew", "auth_token": ticket("s1", "u1", ttl=60)}))
    assert messages(renewed, lambda m: m.get("type") == "auth.renewed")[-1]["type"] == "auth.renewed"
    time.sleep(3)                                                        # past the first ticket's expiry
    frame(renewed)
    assert messages(renewed, lambda m: m.get("type") == "detections.result")[-1]["type"] == "detections.result"
    renewed.close()


def test_renewal_with_another_users_ticket_is_refused(server):
    base, _ = server
    ws = connect(base, ticket("s1", "u1"))
    messages(ws, lambda m: m.get("state") == "ready")
    ws.send(json.dumps({"type": "session.renew", "auth_token": ticket("s1", "intruder")}))
    got = messages(ws, lambda m: m.get("type") in ("error", "closed"))
    assert any(m.get("code") == "unauthorized" for m in got)


def test_invalid_ticket_never_binds(server):
    base, _ = server
    ws = connect(base, ticket("s1", "u1", secret="forged"))
    got = messages(ws, lambda m: m.get("type") in ("error", "closed"))
    assert any(m.get("code") == "unauthorized" for m in got)


# --- MS-05: server-side camera evidence (GET /activity) ---------------------------------------------------


def test_activity_records_real_frames_only(server, monkeypatch):
    """Only decoded JPEG frames of an authorized live connection count as camera evidence."""
    base, client = server
    monkeypatch.setattr(live_ws, "_frame_activity", {})
    assert client.get("/activity").get_json() == {"ok": True, "sessions": []}
    ws = connect(base, ticket("s1", "u-activity"))
    messages(ws, lambda m: m.get("state") == "ready")
    ws.send(json.dumps({"type": "frame.submit", "frame_id": "bad", "image_base64": "bm90IGEganBlZw=="}))
    messages(ws, lambda m: m.get("code") == "invalid_frame")
    assert client.get("/activity").get_json()["sessions"] == []          # a corrupt frame is no evidence
    frame(ws, "good-1")
    messages(ws, lambda m: m.get("type") == "detections.result")
    frame(ws, "good-2")
    messages(ws, lambda m: m.get("type") == "detections.result")
    ws.close()
    (entry,) = client.get("/activity").get_json()["sessions"]
    assert entry["session_id"] == "s1" and entry["frames"] == 2 and entry["last_frame_at"].endswith("Z")
    assert set(entry) == {"session_id", "frames", "last_frame_at"}        # no images, users or tickets


def test_unauthorized_connection_records_no_activity(server, monkeypatch):
    base, client = server
    monkeypatch.setattr(live_ws, "_frame_activity", {})
    ws = connect(base, "forged.ticket.0.0.0")
    frame(ws, "f-unauthorized")
    messages(ws, lambda m: m.get("type") in ("error", "closed"), timeout=3)
    assert client.get("/activity").get_json()["sessions"] == []
