"""Smoke: ring freeze + post window + local MP4; YOLO-path push stays non-blocking."""

from __future__ import annotations

import time
import uuid

import cv2
import numpy as np

from assembler import finalize_clip, start_clip_job, append_live_frame
from ring_buffer import FrameItem, RingRegistry


def _jpeg(color: tuple[int, int, int], text: str) -> bytes:
    img = np.zeros((120, 160, 3), dtype=np.uint8)
    img[:] = color
    cv2.putText(img, text, (10, 70), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1)
    ok, buf = cv2.imencode(".jpg", img, [int(cv2.IMWRITE_JPEG_QUALITY), 80])
    assert ok
    return buf.tobytes()


def main() -> None:
    sid = f"smoke-{uuid.uuid4().hex[:8]}"
    rings = RingRegistry(max_seconds=6.0)
    ring = rings.get(sid)

    t0 = time.time()
    for i in range(20):
        ring.push(_jpeg((20, 80, 20), f"pre{i}"), ts=t0 + i * 0.2)

    # Non-blocking push simulation: filling ring must stay cheap.
    t_push0 = time.perf_counter()
    for i in range(50):
        ring.push(_jpeg((10, 10, 80), f"hot{i}"), ts=t0 + 4.0 + i * 0.01)
    push_ms = (time.perf_counter() - t_push0) * 1000.0
    assert push_ms < 200.0, f"ring push too slow: {push_ms:.1f} ms"

    job = start_clip_job(
        ring,
        reason="phase",
        phase_id="phase-smoke",
        occurred_at="2026-01-01T00:00:00Z",
        pre_seconds=4.0,
        post_seconds=0.3,
    )
    assert len(job.frames) >= 5
    # Post window frames
    for i in range(5):
        append_live_frame(
            job,
            FrameItem(ts=job.post_until - 0.2 + i * 0.05, jpeg=_jpeg((80, 20, 20), f"post{i}")),
        )
    meta = finalize_clip(job)
    assert meta["local_path"].endswith(".mp4")
    assert meta["frame_count"] >= 5
    print("OK smoke_ring", meta["local_path"], "frames=", meta["frame_count"], f"push_ms={push_ms:.1f}")


if __name__ == "__main__":
    main()
