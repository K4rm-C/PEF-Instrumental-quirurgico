"""E2E ONLY — simulated camera source. Plays the role of the Operator's browser camera in the portal capture
page (capture-live-ws.js): gets the portal's frame-submit ticket, binds the VisionWorker live channel and sends
JPEG frames. Every frame carries the text "SIMULACION" and encodes instruments as rectangles (see
vision_sim_worker.py). It is never a physical camera.
"""
from __future__ import annotations

import base64
import json
import threading
import time
import uuid

import cv2
import numpy as np
import simple_websocket


def make_frame(class_ids: list[int], seq: int = 0) -> bytes:
    img = np.full((720, 1280, 3), (34, 30, 26), np.uint8)
    cv2.rectangle(img, (30, 30), (1250, 690), (70, 62, 54), 3)
    for i, cid in enumerate(class_ids):
        col, row = i % 6, i // 6
        x, y = 70 + col * 195, 70 + row * 110
        cv2.rectangle(img, (x, y), (x + 150, y + 46), (20 + 3 * cid, 210, 80), -1)
    cv2.putText(img, f"SIMULACION  frame {seq}", (60, 660), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (120, 120, 120), 2)
    ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 92])
    assert ok
    return buf.tobytes()


class SimulatedCamera:
    """Streams frames for `counts` ({family_code: n}) at ~4 fps over the portal-authorised live WebSocket."""

    def __init__(self, ws_url: str, session_id: str, ticket: str, class_ids: dict[str, list[int]]):
        self.ws = simple_websocket.Client.connect(ws_url)
        self.session_id = session_id
        self.class_ids = class_ids
        self.counts: dict[str, int] = {}
        self.sent = 0
        self.results = 0
        self.last_counts: dict[str, int] = {}
        self._stop = threading.Event()
        self.ws.send(json.dumps({"type": "session.bind", "session_id": session_id, "auth_token": ticket}))
        first = json.loads(self.ws.receive(timeout=10))
        if first.get("type") != "worker.status":
            raise RuntimeError(f"bind refused: {first}")
        self._reader = threading.Thread(target=self._read, daemon=True)
        self._writer = threading.Thread(target=self._write, daemon=True)
        self._reader.start()
        self._writer.start()

    def set_counts(self, counts: dict[str, int]) -> None:
        self.counts = dict(counts)

    def _ids(self) -> list[int]:
        out = []
        for code, n in sorted(self.counts.items()):
            ids = self.class_ids.get(code) or []
            if ids:
                out += [ids[0]] * n
        return out

    def _write(self) -> None:
        while not self._stop.is_set():
            if self.counts is not None:
                jpeg = make_frame(self._ids(), self.sent)
                try:
                    self.ws.send(json.dumps({"type": "frame.submit", "frame_id": str(uuid.uuid4()),
                                             "image_base64": base64.b64encode(jpeg).decode()}))
                    self.sent += 1
                except Exception:  # noqa: BLE001
                    return
            time.sleep(0.25)

    def _read(self) -> None:
        while not self._stop.is_set():
            try:
                raw = self.ws.receive(timeout=1)
            except Exception:  # noqa: BLE001
                return
            if raw is None:
                continue
            msg = json.loads(raw)
            if msg.get("type") == "detections.result":
                self.results += 1
                self.last_counts = {c["family_code"]: c["detected_quantity"] for c in msg.get("counts_by_family", [])}

    def stop(self) -> None:
        self._stop.set()
        try:
            self.ws.send(json.dumps({"type": "session.cancel"}))
            self.ws.close()
        except Exception:  # noqa: BLE001
            pass
