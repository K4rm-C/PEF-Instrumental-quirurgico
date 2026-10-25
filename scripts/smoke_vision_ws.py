"""Smoke: bind + WS frame.submit → detections.result with latency_ms.

Requires VisionWorker on :5002 (same VISION_WS_SECRET as this process).

  cd apps/VisionWorker
  python ../../scripts/smoke_vision_ws.py
"""
from __future__ import annotations

import base64
import json
import sys
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKER = ROOT / "apps" / "VisionWorker"
sys.path.insert(0, str(WORKER))

import cv2  # noqa: E402
import numpy as np  # noqa: E402
import requests  # noqa: E402

from config import WS_SECRET  # noqa: E402
from ws_ticket import verify_ticket  # noqa: E402


def _issue_ticket(session_id: str) -> str:
    import hashlib
    import hmac

    exp = int(time.time()) + 120
    nonce = uuid.uuid4().hex
    msg = f"{session_id}:{exp}:{nonce}".encode("utf-8")
    sig = hmac.new(WS_SECRET.encode("utf-8"), msg, hashlib.sha256).hexdigest()
    ticket = f"{session_id}.{exp}.{nonce}.{sig}"
    assert verify_ticket(ticket, session_id=session_id, secret=WS_SECRET)
    return ticket


def _tiny_jpeg_b64() -> str:
    frame = np.zeros((96, 128, 3), dtype=np.uint8)
    frame[:] = (40, 40, 40)
    ok, buf = cv2.imencode(".jpg", frame)
    if not ok:
        raise RuntimeError("jpeg encode failed")
    return base64.b64encode(buf.tobytes()).decode("ascii")


def main() -> int:
    base = "http://127.0.0.1:5002"
    try:
        health = requests.get(f"{base}/health", timeout=3).json()
    except requests.RequestException as exc:
        print("SKIP: VisionWorker not reachable:", exc)
        return 0
    print("health", health.get("model_device"), "ws", health.get("ws_path"))

    session_id = str(uuid.uuid4())
    bind = requests.post(
        f"{base}/sessions/{session_id}/bind",
        json={"expected": [], "class_map": [], "model_version": "yolo26l-demo"},
        timeout=30,
    )
    print("bind", bind.status_code)
    if bind.status_code >= 400:
        print("FAIL bind", bind.text)
        return 1

    try:
        from simple_websocket import Client
    except ImportError:
        print("FAIL: simple_websocket missing (install flask-sock)")
        return 1

    ticket = _issue_ticket(session_id)
    ws = Client.connect("ws://127.0.0.1:5002/ws")
    try:
        ws.send(json.dumps({
            "type": "session.bind",
            "session_id": session_id,
            "auth_token": ticket,
        }))
        ready = json.loads(ws.receive())
        print("bind_reply", ready.get("type"), ready.get("state") or ready.get("code"))
        if ready.get("type") == "error":
            print("FAIL", ready)
            return 1
        ws.send(json.dumps({
            "type": "frame.submit",
            "session_id": session_id,
            "frame_id": str(uuid.uuid4()),
            "captured_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "image_base64": _tiny_jpeg_b64(),
            "image_content_type": "image/jpeg",
        }))
        deadline = time.time() + 60
        result = None
        while time.time() < deadline:
            raw = ws.receive()
            if raw is None:
                break
            msg = json.loads(raw)
            print("msg", msg.get("type"), msg.get("state") or msg.get("code"), msg.get("latency_ms"))
            if msg.get("type") == "detections.result":
                result = msg
                break
            if msg.get("type") == "error":
                print("FAIL", msg)
                return 1
        if not result:
            print("FAIL: no detections.result")
            return 1
        lat = float(result.get("latency_ms") or 0)
        assert lat > 0, lat
        assert result.get("session", {}).get("capture_source") == "live"
        print("OK latency_ms", lat)
        return 0
    finally:
        try:
            ws.close()
        except Exception:
            pass
        requests.post(f"{base}/sessions/{session_id}/unbind", timeout=5)


if __name__ == "__main__":
    raise SystemExit(main())
