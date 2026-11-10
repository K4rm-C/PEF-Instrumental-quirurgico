"""Smoke: evidence JPEG push must stay cheap (no await of encode/Garage)."""

from __future__ import annotations

import base64
import time

import cv2
import numpy as np

# Force queue off so this smoke does not need Redis.
import config as cfg

cfg.EVIDENCE_QUEUE_ENABLED = False

from evidence_queue import push_camera_jpeg  # noqa: E402


def main() -> None:
    img = np.zeros((64, 64, 3), dtype=np.uint8)
    ok, buf = cv2.imencode(".jpg", img)
    assert ok
    b64 = base64.b64encode(buf.tobytes()).decode("ascii")
    t0 = time.perf_counter()
    for _ in range(200):
        push_camera_jpeg("smoke-session", b64, captured_at=time.time())
    ms = (time.perf_counter() - t0) * 1000.0
    assert ms < 100.0, f"push too slow even when disabled: {ms:.1f} ms"
    print(f"OK smoke_evidence_queue disabled_push_ms={ms:.1f}")


if __name__ == "__main__":
    main()
