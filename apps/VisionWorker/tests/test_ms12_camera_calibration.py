"""MS-12 (ms-camaras): the technician-measured calibration that the portal puts in the signed bind is kept typed and
bounded by the worker, exposed with the tracking summary, and never trusted when malformed (no physical parameter
is invented or derived by the worker)."""
from __future__ import annotations

import main

BASE = {"id": "c1", "name": "Overhead", "device_type": "usb", "resolution_width": 1280, "resolution_height": 720,
        "fps": 30, "roi": None, "simulated": False}


def test_a_valid_calibration_is_kept_typed():
    cal = {"id": "a" * 36, "mm_per_pixel": "0.25", "frame_width": "1280", "frame_height": 720, "performed_at": "x"}
    out = main._camera_config({**BASE, "calibration": cal})
    assert out["calibration"] == {"id": "a" * 36, "mm_per_pixel": 0.25, "frame_width": 1280, "frame_height": 720}


def test_malformed_or_absent_calibrations_are_dropped():
    for cal in (None, "0.25", {"mm_per_pixel": "x", "frame_width": 1280, "frame_height": 720},
                {"mm_per_pixel": 0, "frame_width": 1280, "frame_height": 720},
                {"mm_per_pixel": -1, "frame_width": 1280, "frame_height": 720},
                {"mm_per_pixel": 0.2, "frame_width": 10, "frame_height": 720},
                {"mm_per_pixel": 0.2, "frame_height": 720}):
        assert "calibration" not in main._camera_config({**BASE, "calibration": cal})
