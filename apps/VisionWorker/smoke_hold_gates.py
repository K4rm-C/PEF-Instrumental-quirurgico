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

    # 1-2 live frames: no hold yet; table waits for seed (anti-flicker)
    for t in (0.0, 0.2):
        out, boxes = _apply_hold(
            state,
            counts=_counts("FOERSTER", 1),
            public_boxes=[_box("FOERSTER", xy)],
            video_t=t,
        )
        assert "FOERSTER" not in state._hold_by_family
        assert int(out["FOERSTER"]["detected_quantity"]) == 0
        assert out["FOERSTER"].get("pending_promote") is True

    # 3rd live frame seeds hold and promotes to the table
    out, _ = _apply_hold(
        state,
        counts=_counts("FOERSTER", 1),
        public_boxes=[_box("FOERSTER", xy)],
        video_t=0.4,
    )
    assert "FOERSTER" in state._hold_by_family
    assert int(out["FOERSTER"]["detected_quantity"]) == 1
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

    # Named sticky: confirmed absence shrinks count, not name_until.
    state_sticky = SessionState(session_id="smoke-hold-sticky-name")
    state_sticky.expected = state.expected
    state_sticky.hold_seed_frames = 3
    state_sticky.hold_absent_streak = 3
    state_sticky.hold_absent_seconds = 1.1
    state_sticky.hold_seconds = 2.5
    state_sticky.name_hold_seconds = 12.0
    state_sticky.hold_zone_iou = 0.15
    for t in (0.0, 0.2, 0.4):
        _apply_hold(
            state_sticky,
            counts=_counts("FOERSTER", 1),
            public_boxes=[_box("FOERSTER", xy)],
            video_t=t,
        )
    name_until0 = float(state_sticky._hold_by_family["FOERSTER"]["name_until"])
    for t in (0.6, 0.8, 1.0):
        _apply_hold(
            state_sticky,
            counts=_counts("FOERSTER", 0),
            public_boxes=[],
            video_t=t,
        )
    assert float(state_sticky._hold_by_family["FOERSTER"]["name_until"]) >= name_until0 - 1e-6
    assert float(state_sticky._hold_by_family["FOERSTER"]["until"]) <= 1.0 + 1.11 + 1e-6

    # Confusion hold: top2–N of a box whose top1 is already assigned → 5 s renew.
    # Top1 alone must not seed confusion.
    state_cf = SessionState(session_id="smoke-hold-confusion")
    state_cf.expected = {
        "FOERSTER": {
            "family_id": "f1",
            "family_name": "Foerster",
            "expected_quantity": 1,
            "category_name": "Grasping",
        },
        "ALLIS": {
            "family_id": "f2",
            "family_name": "Allis",
            "expected_quantity": 1,
            "category_name": "Grasping",
        },
    }
    state_cf.confusion_hold_seconds = 5.0
    state_cf.hold_seed_frames = 3
    state_cf.hold_seconds = 2.5
    state_cf.name_hold_seconds = 12.0
    foerster_box = {
        "xyxy": xy,
        "family_code": "FOERSTER",
        "family_name": "Foerster",
        "label": "Foerster",
        "confidence": 0.92,
        "high_confidence": True,
        "resolution_reason": "kept_raw",
        "raw": {
            "family_code": "FOERSTER",
            "confidence": 0.92,
            "topk": [
                {"family_code": "FOERSTER", "family_name": "Foerster", "score": 0.92},
                {"family_code": "ALLIS", "family_name": "Allis", "score": 0.61},
            ],
        },
    }
    counts_live = {
        **_counts("FOERSTER", 1),
        "ALLIS": {
            "family_id": "f2",
            "family_code": "ALLIS",
            "family_name": "Allis",
            "expected_quantity": 1,
            "detected_quantity": 0,
            "raw_detected_quantity": 0,
            "difference": -1,
            "best_confidence": None,
        },
    }
    out, boxes = _apply_hold(
        state_cf,
        counts=counts_live,
        public_boxes=[foerster_box],
        video_t=0.0,
    )
    assert "ALLIS" in state_cf._confusion_by_family
    assert out["ALLIS"].get("confusion_hold") is True
    assert int(out["ALLIS"]["detected_quantity"]) == 1
    assert any(b.get("confusion_hold") for b in boxes)
    until_cf = float(state_cf._confusion_by_family["ALLIS"]["until"])
    assert abs(until_cf - 5.0) < 1e-6

    # Renew while still in top2–N
    out, _ = _apply_hold(
        state_cf,
        counts=counts_live,
        public_boxes=[foerster_box],
        video_t=2.0,
    )
    assert float(state_cf._confusion_by_family["ALLIS"]["until"]) >= 6.99

    # Expire after 5 s without renew
    out, _ = _apply_hold(
        state_cf,
        counts=counts_live,
        public_boxes=[{
            **foerster_box,
            "raw": {
                "family_code": "FOERSTER",
                "confidence": 0.92,
                "topk": [
                    {"family_code": "FOERSTER", "family_name": "Foerster", "score": 0.92},
                ],
            },
        }],
        video_t=8.0,
    )
    assert "ALLIS" not in state_cf._confusion_by_family
    assert int(out["ALLIS"]["detected_quantity"]) == 0

    # Top1-only must never seed confusion for FOERSTER when it is top1.
    state_top1 = SessionState(session_id="smoke-hold-no-top1-confusion")
    state_top1.expected = state_cf.expected
    state_top1.confusion_hold_seconds = 5.0
    empty_allis = {
        "FOERSTER": {
            "family_id": "f1",
            "family_code": "FOERSTER",
            "family_name": "Foerster",
            "expected_quantity": 1,
            "detected_quantity": 0,
            "raw_detected_quantity": 0,
            "difference": -1,
        },
        "ALLIS": {
            "family_id": "f2",
            "family_code": "ALLIS",
            "family_name": "Allis",
            "expected_quantity": 1,
            "detected_quantity": 0,
            "raw_detected_quantity": 0,
            "difference": -1,
        },
    }
    orphan = {
        "xyxy": xy,
        "family_code": None,
        "label": "Grasping",
        "confidence": 0.5,
        "high_confidence": False,
        "resolution_reason": "type_degraded",
        "raw": {
            "family_code": "FOERSTER",
            "confidence": 0.5,
            "topk": [
                {"family_code": "FOERSTER", "family_name": "Foerster", "score": 0.5},
            ],
        },
    }
    _apply_hold(state_top1, counts=empty_allis, public_boxes=[orphan], video_t=0.0)
    assert "FOERSTER" not in state_top1._confusion_by_family

    # Enter floor: ≤0.49 never seeds hold; >0.49 can.
    state_enter = SessionState(session_id="smoke-hold-enter-floor")
    state_enter.expected = state.expected
    state_enter.hold_seed_frames = 3
    state_enter.hold_seconds = 1.0
    state_enter.name_hold_seconds = 1.0
    state_enter.hold_min_score = 0.50
    state_enter.hold_drop_score = 0.35
    weak49 = _box("FOERSTER", xy, high=False)
    weak49["confidence"] = 0.49
    for t in (0.0, 0.2, 0.4):
        _apply_hold(
            state_enter,
            counts=_counts("FOERSTER", 1),
            public_boxes=[weak49],
            video_t=t,
        )
    assert "FOERSTER" not in state_enter._hold_by_family
    ok51 = _box("FOERSTER", xy, high=False)
    ok51["confidence"] = 0.51
    for t in (0.6, 0.8, 1.0):
        _apply_hold(
            state_enter,
            counts=_counts("FOERSTER", 1),
            public_boxes=[ok51],
            video_t=t,
        )
    assert "FOERSTER" in state_enter._hold_by_family

    # Drop floor: one weak frame keeps hold (hysteresis); streak clears it.
    state_drop = SessionState(session_id="smoke-hold-drop-floor")
    state_drop.expected = state.expected
    state_drop.hold_seed_frames = 3
    state_drop.hold_seconds = 2.0
    state_drop.name_hold_seconds = 2.0
    state_drop.hold_min_score = 0.50
    state_drop.hold_drop_score = 0.35
    state_drop.hold_drop_streak = 3
    for t in (0.0, 0.2, 0.4):
        _apply_hold(
            state_drop,
            counts=_counts("FOERSTER", 1),
            public_boxes=[_box("FOERSTER", xy)],
            video_t=t,
        )
    assert "FOERSTER" in state_drop._hold_by_family
    collapsed = _box("FOERSTER", xy, high=False)
    collapsed["confidence"] = 0.34
    _apply_hold(
        state_drop,
        counts=_counts("FOERSTER", 1),
        public_boxes=[collapsed],
        video_t=0.6,
    )
    assert "FOERSTER" in state_drop._hold_by_family
    assert state_drop._hold_drop_weak_streak.get("FOERSTER") == 1
    for t in (0.8, 1.0):
        _apply_hold(
            state_drop,
            counts=_counts("FOERSTER", 1),
            public_boxes=[collapsed],
            video_t=t,
        )
    assert "FOERSTER" not in state_drop._hold_by_family

    # Soft-name: mid-conf assigned Type with open quota → name after dwell.
    from inference import _apply_soft_names

    state_soft = SessionState(session_id="smoke-soft-name")
    state_soft.expected = {
        "ADSON": {
            "family_id": "a1",
            "family_code": "ADSON",
            "family_name": "Adson",
            "category_name": "Grasping",
            "expected_quantity": 2,
        }
    }
    state_soft.soft_name_seconds = 1.5
    state_soft.soft_name_min_score = 0.48
    strong = {
        "xyxy": [10.0, 10.0, 40.0, 80.0],
        "family_code": "ADSON",
        "family_name": "Adson",
        "category_name": "Grasping",
        "label": "Adson",
        "confidence": 0.88,
        "high_confidence": True,
        "track_id": 1,
    }
    weak = {
        "xyxy": [60.0, 10.0, 90.0, 80.0],
        "family_code": "ADSON",
        "family_name": "Adson",
        "category_name": "Grasping",
        "label": "Grasping",
        "confidence": 0.50,
        "high_confidence": False,
        "track_id": 2,
    }
    soft_counts = {
        "ADSON": {
            "family_code": "ADSON",
            "expected_quantity": 2,
            "detected_quantity": 2,
            "difference": 0,
            "box_confidences": [0.88, 0.50],
        }
    }
    boxes_soft = [dict(strong), dict(weak)]
    assert _apply_soft_names(
        state_soft, boxes=boxes_soft, counts=soft_counts, video_t=0.0
    ) == 0
    assert boxes_soft[1]["high_confidence"] is False
    assert _apply_soft_names(
        state_soft, boxes=boxes_soft, counts=soft_counts, video_t=1.0
    ) == 0
    n = _apply_soft_names(
        state_soft, boxes=boxes_soft, counts=soft_counts, video_t=1.6
    )
    assert n == 1
    assert boxes_soft[1]["high_confidence"] is True
    assert boxes_soft[1].get("soft_named") is True
    assert boxes_soft[1]["label"] == "Adson"

    # No soft-name when quota names are already filled.
    state_full = SessionState(session_id="smoke-soft-full")
    state_full.expected = state_soft.expected
    state_full.soft_name_seconds = 0.5
    state_full.soft_name_min_score = 0.48
    full_boxes = [
        dict(strong),
        {
            **dict(weak),
            "track_id": 3,
            "high_confidence": False,
            "label": "Grasping",
            "confidence": 0.52,
        },
    ]
    # expected_quantity 2 but both already named → no promote for a third Type.
    state_full.expected = {
        "ADSON": {
            "family_id": "a1",
            "family_code": "ADSON",
            "family_name": "Adson",
            "category_name": "Grasping",
            "expected_quantity": 1,
        }
    }
    full_counts = {
        "ADSON": {
            "family_code": "ADSON",
            "expected_quantity": 1,
            "detected_quantity": 1,
            "difference": 0,
        }
    }
    for t in (0.0, 0.6, 1.2):
        assert (
            _apply_soft_names(
                state_full, boxes=full_boxes, counts=full_counts, video_t=t
            )
            == 0
        )
    assert full_boxes[1]["high_confidence"] is False

    print("OK smoke_hold_gates")


if __name__ == "__main__":
    main()
