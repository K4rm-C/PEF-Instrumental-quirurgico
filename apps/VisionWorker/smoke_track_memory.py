"""Smoke: track EMA, contention to a free family, overlay quota, memory rollback."""
from __future__ import annotations

from assign import assign_boxes
from inference import _overlay_quota_boxes
from tracks import commit_track_families, update_box_tracks

CLASS_MAP = {
    0: {"family_code": "FOERSTER", "family_id": "f1", "family_name": "Foerster", "category_name": "Grasping"},
    1: {"family_code": "ALLIS", "family_id": "f2", "family_name": "Allis", "category_name": "Grasping"},
}
EXPECTED = {
    "FOERSTER": {"family_id": "f1", "family_name": "Foerster", "category_name": "Grasping", "expected_quantity": 1},
    "ALLIS": {"family_id": "f2", "family_name": "Allis", "category_name": "Grasping", "expected_quantity": 1},
}


def _box(xyxy, scores, class_id: int) -> dict:
    top = max(range(len(scores)), key=lambda i: scores[i])
    return {
        "xyxy": xyxy,
        "class_id": class_id,
        "confidence": float(scores[class_id]),
        "scores_by_class": list(scores),
        "topk": [
            {"class_id": i, "score": scores[i], "model_name": CLASS_MAP[i]["family_name"]}
            for i in sorted(range(len(scores)), key=lambda j: scores[j], reverse=True)
        ],
        "model_name": CLASS_MAP[class_id]["family_name"],
    }


def main() -> None:
    # Gap > 0.35 and below reassign min → Type degrade without memory.
    a = _box([0, 0, 40, 80], [0.85, 0.40], 0)
    b_far = _box([80, 0, 120, 80], [0.80, 0.40], 0)

    off, counts_off, _ = assign_boxes(
        [a, b_far], expected=EXPECTED, class_map=CLASS_MAP, memory=False
    )
    named_off = [x.get("family_code") for x in off if x.get("family_code")]
    assert named_off.count("FOERSTER") == 1
    assert "ALLIS" not in named_off
    assert int(counts_off["FOERSTER"]["detected_quantity"]) == 1
    assert int(counts_off["ALLIS"]["detected_quantity"]) == 0
    assert any(x.get("resolution_reason") == "type_degraded" for x in off)

    b = _box([80, 0, 120, 80], [0.80, 0.52], 0)
    on, counts_on, metrics = assign_boxes(
        [a, b], expected=EXPECTED, class_map=CLASS_MAP, memory=True
    )
    named_on = [x.get("family_code") for x in on if x.get("family_code")]
    assert named_on.count("FOERSTER") == 1
    assert "ALLIS" in named_on
    assert int(counts_on["FOERSTER"]["detected_quantity"]) == 1
    assert int(counts_on["ALLIS"]["detected_quantity"]) == 1
    assert metrics.get("track_memory") is True

    # Overlay: no duplicate family name; Type extras still draw.
    overlay = _overlay_quota_boxes(on, counts_on)
    labels = [x.get("family_code") for x in overlay]
    assert labels.count("FOERSTER") == 1
    assert labels.count("ALLIS") == 1
    extra = {
        "xyxy": [10, 10, 30, 70],
        "family_code": "FOERSTER",
        "label": "Foerster",
        "confidence": 0.4,
        "high_confidence": False,
        "sticky_name": True,
    }
    type_extra = {
        "xyxy": [200, 0, 240, 80],
        "family_code": None,
        "label": "Grasping",
        "confidence": 0.5,
        "high_confidence": False,
        "resolution_reason": "type_degraded",
    }
    bloated = overlay + [extra, type_extra]
    capped = _overlay_quota_boxes(bloated, counts_on)
    assert sum(1 for x in capped if x.get("family_code") == "FOERSTER") == 1
    assert any(
        (not x.get("family_code") and x.get("label") == "Grasping")
        for x in capped
    )

    # EMA follows the track, not a single spike.
    t0 = _box([0, 0, 50, 90], [0.90, 0.10], 0)
    tracks = update_box_tracks([], [t0], video_t=0.0, enabled=True, alpha=0.35)
    t1 = _box([2, 2, 52, 92], [0.20, 0.80], 1)
    tracks = update_box_tracks(tracks, [t1], video_t=0.06, enabled=True, alpha=0.35)
    ema = t1.get("ema_scores") or []
    assert len(ema) == 2
    assert ema[0] > 0.20  # still remembers Foerster
    assert ema[1] < 0.80
    commit_track_families(tracks, [{"track_id": 0, "family_code": "FOERSTER"}], enabled=True)
    assert tracks[0].get("family_code") == "FOERSTER"

    empty = update_box_tracks(tracks, [t1], video_t=0.12, enabled=False)
    assert empty == []
    assert "ema_scores" not in t1

    print("OK smoke_track_memory")


if __name__ == "__main__":
    main()
