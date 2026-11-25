"""E2E ONLY — runs the REAL VisionWorker app (apps/VisionWorker: /bind, /ws live channel, assignment, holds,
tracking, annotated frames, the ELECTRON-01 observe-only monitor) with ONE substitution: the YOLO detector is
replaced by a deterministic synthetic detector, because this environment has no best.pt weights and no GPU.

THIS IS A SIMULATION. It never loads a model, never validates hardware and must never be presented as a
physical camera or as YOLO accuracy. Frames come from e2e/camera_sim.py: each "instrument" is a filled
rectangle whose blue level encodes a YOLO class id; the fake detector finds the rectangles and reports one box
per rectangle with confidence 0.93.

usage: python e2e/vision_sim_worker.py      (listens on 127.0.0.1:5002, like the host VisionWorker)
env:   VISION_MONITOR_SECRET, VISION_WS_SECRET (same values as the compose .env), REDIS_URL (optional)
"""
from __future__ import annotations

import os
import sys
import tempfile
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
VISION = ROOT / "apps" / "VisionWorker"
sys.path.insert(0, str(VISION))
os.environ.setdefault("VISION_UPLOAD_DIR", tempfile.mkdtemp(prefix="pef-e2e-vision-"))
os.environ.setdefault("VISION_METRICS_DIR", tempfile.mkdtemp(prefix="pef-e2e-metrics-"))

try:  # torch is only needed by the real detector; a stub is enough for imports when it is absent
    import torch  # noqa: F401
except ImportError:
    stub = types.ModuleType("torch")

    class _Cuda:
        @staticmethod
        def is_available():
            return False

        @staticmethod
        def device_count():
            return 0

        @staticmethod
        def get_device_name(_i=0):
            return "-"

    stub.cuda = _Cuda()
    stub.device = lambda kind: types.SimpleNamespace(type=kind)
    stub.is_tensor = lambda _x: False
    sys.modules["torch"] = stub

import cv2  # noqa: E402
import numpy as np  # noqa: E402
from flask import jsonify  # noqa: E402

import inference  # noqa: E402
import main  # noqa: E402

N_CLASSES = 64
CONF = 0.93


def fake_detect(_model, frame_bgr, **_kw):
    """Rectangles with G≈210 are instruments; class id = round((B - 20) / 3)."""
    hsv_mask = (frame_bgr[:, :, 1] > 150).astype(np.uint8) * 255
    contours, _ = cv2.findContours(hsv_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    boxes = []
    for c in contours:
        x, y, w, h = cv2.boundingRect(c)
        if w < 20 or h < 10:
            continue
        inner = frame_bgr[y + 3:y + h - 3, x + 3:x + w - 3]
        cid = int(round((float(inner[:, :, 0].mean()) - 20.0) / 3.0))
        cid = max(0, min(N_CLASSES - 1, cid))
        scores = [0.01] * N_CLASSES
        scores[cid] = CONF
        boxes.append({
            "xyxy": [float(x), float(y), float(x + w), float(y + h)],
            "class_id": cid,
            "confidence": CONF,
            "scores_by_class": scores,
            "topk": [{"class_id": cid, "score": CONF, "model_name": str(cid)}],
            "model_name": str(cid),
        })
    return boxes, 1.0


inference.get_model = lambda *a, **k: object()
inference.detect_with_class_scores = fake_detect
main.get_model = inference.get_model


@main.app.get("/__sim__/class-map/<session_id>")
def sim_class_map(session_id):
    """Test helper (simulation only): yolo class ids of each expected family code of a bound session."""
    state = main._get_state(session_id)
    if state is None:
        return jsonify({"ok": False}), 404
    by_family = {}
    for cid, meta in state.class_map.items():
        code = meta.get("family_code")
        if code in state.expected:
            by_family.setdefault(code, []).append(int(cid))
    expected = {code: int(meta.get("expected_quantity") or 0) for code, meta in state.expected.items()}
    return jsonify({"ok": True, "class_ids": by_family, "expected": expected})


@main.app.get("/__sim__/observers")
def sim_observers():
    """Test helper (simulation only): open observe-only connections per session."""
    import live_monitor
    with live_monitor._observers_lock:
        return jsonify(dict(live_monitor._observers))


if __name__ == "__main__":
    from werkzeug.serving import make_server

    print("SIMULATION: real VisionWorker app with a synthetic detector (no YOLO, no physical camera)", flush=True)
    print(f"monitor_ws={main.monitor_enabled()}", flush=True)
    server = make_server("127.0.0.1", int(os.getenv("VISION_SIM_PORT", "5002")), main.app, threaded=True)
    print(f"listening on 127.0.0.1:{server.server_port}", flush=True)
    server.serve_forever()
