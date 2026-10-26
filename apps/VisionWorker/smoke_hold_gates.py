"""Terminal smoke for hold seed / absent TTL / zone occlusion (no YOLO)."""
from __future__ import annotations

from inference import SessionState, _apply_hold


def _counts(code: str, det: int, *, named: bool = True) -> dict:
    return {
        code: {
            "family_id": "f1",
            "family_code": code,
            "family_name": "Tool",
            "expected_quantity": 1,
            "detected_quantity": det,
            "raw_detected_quantity": det,
            "difference": det - 1,
            "best_confidence": 0.9 if det else None,
        }
    }


def _box(code: str, xyxy, *, high: bool = True) -> dict:
    return {
        "xyxy": xyxy,
        "family_code": code,
        "family_name": "Tool",
        "label": "Tool" if high else "Grasping",
        "confidence": 0.9 if high else 0.4,
        "high_confidence": high,
        "resolution_reason": "kept_raw",
    }


def main() -> None:
    state = SessionState(session_id="smoke-hold")
    state.expected = {"FOERSTER": {"family_id": "f1", "expected_quantity": 1}}
    state.hold_seed_frames = 3
    state.hold_absent_streak = 3
    state.hold_absent_seconds = 1.1
    state.hold_seconds = 2.5
    state.name_hold_seconds = 4.0
    state.hold_min_score = 0.35
    state.hold_zone_iou = 0.15

    xy = [100.0, 100.0, 200.0, 300.0]

    # 1-2 live frames: no hold yet
    for t in (0.0, 0.2):
        out, boxes = _apply_hold(
            state,
            counts=_counts("FOERSTER", 1),
            public_boxes=[_box("FOERSTER", xy)],
            video_t=t,
        )
        assert "FOERSTER" not in state._hold_by_family
        assert out["FOERSTER"]["detected_quantity"] == 1

    # 3rd live frame seeds hold
    out, _ = _apply_hold(
        state,
        counts=_counts("FOERSTER", 1),
        public_boxes=[_box("FOERSTER", xy)],
        video_t=0.4,
    )
    assert "FOERSTER" in state._hold_by_family
    until_long = float(state._hold_by_family["FOERSTER"]["until"])
    assert until_long >= 0.4 + 2.4

    # Brief absence (1-2 frames): still long hold, restored
    for t in (0.6, 0.8):
        out, boxes = _apply_hold(
            state,
            counts=_counts("FOERSTER", 0),
            public_boxes=[],
            video_t=t,
        )
        assert out["FOERSTER"].get("held") is True
        assert float(state._hold_by_family["FOERSTER"]["until"]) >= until_long - 0.01

    # 3rd absent without zone overlap → shrink to ~1.1s
    out, _ = _apply_hold(
        state,
        counts=_counts("FOERSTER", 0),
        public_boxes=[],
        video_t=1.0,
    )
    until_short = float(state._hold_by_family["FOERSTER"]["until"])
    assert until_short <= 1.0 + 1.11 + 1e-6
    assert out["FOERSTER"].get("held") is True

    # After absent TTL, hold drops from counts
    out, boxes = _apply_hold(
        state,
        counts=_counts("FOERSTER", 0),
        public_boxes=[],
        video_t=2.2,
    )
    assert int(out["FOERSTER"]["detected_quantity"]) == 0
    assert not out["FOERSTER"].get("held")

    # Re-seed and test zone occlusion: delays shrink, does not restart the clock.
    state2 = SessionState(session_id="smoke-hold-zone")
    state2.expected = state.expected
    state2.hold_seed_frames = 3
    state2.hold_absent_streak = 3
    state2.hold_absent_seconds = 1.1
    state2.hold_seconds = 2.5
    state2.name_hold_seconds = 4.0
    state2.hold_zone_iou = 0.15
    for t in (0.0, 0.2, 0.4):
        _apply_hold(
            state2,
            counts=_counts("FOERSTER", 1),
            public_boxes=[_box("FOERSTER", xy)],
            video_t=t,
        )
    until0 = float(state2._hold_by_family["FOERSTER"]["until"])
    # Family count 0 but overlapping type box in same place
    type_box = {
        "xyxy": [110.0, 110.0, 190.0, 280.0],
        "family_code": None,
        "label": "Grasping",
        "confidence": 0.5,
        "high_confidence": False,
        "resolution_reason": "type_degraded",
    }
    for t in (0.6, 0.8, 1.0):
        out, _ = _apply_hold(
            state2,
            counts=_counts("FOERSTER", 0),
            public_boxes=[type_box],
            video_t=t,
        )
    until_zone = float(state2._hold_by_family["FOERSTER"]["until"])
    assert abs(until_zone - until0) < 1e-6
    assert out["FOERSTER"].get("held") is True
    # Count timeout still wins; name sticks to the live type box in the slot.
    out, boxes = _apply_hold(
        state2,
        counts=_counts("FOERSTER", 0),
        public_boxes=[type_box],
        video_t=until0 + 0.05,
    )
    assert not out["FOERSTER"].get("held")
    sticky = [b for b in boxes if b.get("sticky_name")]
    assert sticky and sticky[0].get("label") == "Tool"

    # Live weak conf (<0.70) for 6s must keep the family name, not drop to Type.
    state_name = SessionState(session_id="smoke-hold-weak-name")
    state_name.expected = state.expected
    state_name.hold_seed_frames = 3
    state_name.hold_absent_streak = 3
    state_name.hold_seconds = 2.5
    state_name.name_hold_seconds = 4.0
    state_name.hold_min_score = 0.35
    state_name.hold_zone_iou = 0.15
    weak = _box("FOERSTER", xy, high=False)
    weak["confidence"] = 0.55
    for t in (0.0, 0.2, 0.4):
        _apply_hold(
            state_name,
            counts=_counts("FOERSTER", 1),
            public_boxes=[_box("FOERSTER", xy)],
            video_t=t,
        )
    until_name = float(state_name._hold_by_family["FOERSTER"]["until"])
    out, boxes = _apply_hold(
        state_name,
        counts=_counts("FOERSTER", 1),
        public_boxes=[weak],
        video_t=6.4,
    )
    assert out["FOERSTER"].get("sticky_name") is True
    assert boxes and boxes[0].get("label") == "Tool" and boxes[0].get("sticky_name")
    assert float(state_name._hold_by_family["FOERSTER"]["until"]) <= until_name + 0.01

    # Neighbor family in the slot must not keep this family's long TTL / name.
    state_nb = SessionState(session_id="smoke-hold-neighbor")
    state_nb.expected = {
        "FOERSTER": {"family_id": "f1", "expected_quantity": 1},
        "ALLIS": {"family_id": "f2", "expected_quantity": 1},
    }
    state_nb.hold_seed_frames = 3
    state_nb.hold_absent_streak = 3
    state_nb.hold_absent_seconds = 1.1
    state_nb.hold_seconds = 2.5
    state_nb.name_hold_seconds = 4.0
    state_nb.hold_zone_iou = 0.15
    for t in (0.0, 0.2, 0.4):
        _apply_hold(
            state_nb,
            counts={**_counts("FOERSTER", 1), **_counts("ALLIS", 0)},
            public_boxes=[_box("FOERSTER", xy)],
            video_t=t,
        )
    until_nb = float(state_nb._hold_by_family["FOERSTER"]["until"])
    neighbor = _box("ALLIS", [110.0, 110.0, 190.0, 280.0])
    neighbor["family_name"] = "Allis"
    neighbor["label"] = "Allis"
    for t in (0.6, 0.8, 1.0):
        out, boxes = _apply_hold(
            state_nb,
            counts={**_counts("FOERSTER", 0), **_counts("ALLIS", 1)},
            public_boxes=[neighbor],
            video_t=t,
        )
    until_after_nb = float(state_nb._hold_by_family["FOERSTER"]["until"])
    assert until_after_nb <= 1.0 + 1.11 + 1e-6
    assert until_after_nb < until_nb - 0.5
    live_labels = [b.get("label") for b in boxes if not b.get("held")]
    assert live_labels and all(lbl != "Tool" for lbl in live_labels)
    out, _ = _apply_hold(
        state_nb,
        counts={**_counts("FOERSTER", 0), **_counts("ALLIS", 1)},
        public_boxes=[neighbor],
        video_t=2.2,
    )
    assert not out["FOERSTER"].get("held")

    # CUDA stride 1: 3 inferences (~0.1 s of tape) must not seed; ~0.5 s must.
    state_cu = SessionState(session_id="smoke-hold-cuda")
    state_cu.expected = state.expected
    state_cu.frame_stride = 1
    state_cu.video_fps = 30.0
    state_cu.hold_seed_frames = 3
    state_cu.hold_absent_streak = 3
    state_cu.hold_absent_seconds = 1.1
    state_cu.hold_seconds = 2.5
    state_cu.hold_min_score = 0.35
    for t in (0.0, 0.033, 0.066):
        _apply_hold(
            state_cu,
            counts=_counts("FOERSTER", 1),
            public_boxes=[_box("FOERSTER", xy)],
            video_t=t,
        )
        assert "FOERSTER" not in state_cu._hold_by_family
    _apply_hold(
        state_cu,
        counts=_counts("FOERSTER", 1),
        public_boxes=[_box("FOERSTER", xy)],
        video_t=0.5,
    )
    assert "FOERSTER" in state_cu._hold_by_family
    until_cu = float(state_cu._hold_by_family["FOERSTER"]["until"])

    # Pickup + 1-frame flicker must not restore the 2.5 s TTL.
    _apply_hold(
        state_cu,
        counts=_counts("FOERSTER", 0),
        public_boxes=[],
        video_t=0.55,
    )
    _apply_hold(
        state_cu,
        counts=_counts("FOERSTER", 1),
        public_boxes=[_box("FOERSTER", xy)],
        video_t=0.58,
    )
    until_flicker = float(state_cu._hold_by_family["FOERSTER"]["until"])
    assert until_flicker <= until_cu + 0.01

    _apply_hold(
        state_cu,
        counts=_counts("FOERSTER", 0),
        public_boxes=[],
        video_t=1.05,
    )
    until_short_cu = float(state_cu._hold_by_family["FOERSTER"]["until"])
    assert until_short_cu <= 1.05 + 1.11 + 1e-6

    print("OK smoke_hold_gates")


if __name__ == "__main__":
    main()
