"""YOLO inference + matrix_v1 greedy assignment for the demo vision worker."""

from __future__ import annotations

import base64
import copy
import json
import threading
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import cv2
import numpy as np

from assign import assign_boxes
from category_colors import overlay_bgr
from config import (
    ASSIGN_SOLVER,
    CONF_DISPLAY_THRESHOLD,
    COUNT_SAMPLE_EVERY,
    EDGE_FRAME_SCAN,
    FRAME_MAX_BRIGHTNESS,
    FRAME_MIN_BRIGHTNESS,
    FRAME_MIN_CONTRAST,
    FRAME_STRIDE,
    HOLD_ABSENT_SECONDS,
    HOLD_ABSENT_STREAK,
    HOLD_DEDUPE_IOU,
    HOLD_MIN_SCORE,
    HOLD_SECONDS,
    HOLD_SEED_FRAMES,
    HOLD_ZONE_IOU,
    METRICS_DIR,
    NAME_HOLD_SECONDS,
    WORKER_PIPELINE,
    WEIGHTS_PATH,
)
from infer_pipeline import detect_with_class_scores
from telemetry import TELEMETRY, utc_now

_model = None
_model_lock = threading.Lock()


def get_model():
    global _model
    with _model_lock:
        if _model is None:
            if not WEIGHTS_PATH.is_file():
                raise FileNotFoundError(
                    f"Weights not found at {WEIGHTS_PATH}. "
                    "Place best.pt under apps/VisionWorker/weights/ (gitignored)."
                )
            from ultralytics import YOLO

            _model = YOLO(str(WEIGHTS_PATH))
        return _model


@dataclass
class SessionState:
    session_id: str
    expected: dict[str, dict[str, Any]] = field(default_factory=dict)
    class_map: dict[int, dict[str, Any]] = field(default_factory=dict)
    status: str = "idle"  # idle | processing | ready | error
    message: str = ""
    progress: float = 0.0
    frame_jpeg_b64: str | None = None
    boxes: list[dict[str, Any]] = field(default_factory=list)
    counts: dict[str, dict[str, Any]] = field(default_factory=dict)
    raw_counts: dict[str, dict[str, Any]] = field(default_factory=dict)
    assignment_metrics: dict[str, Any] = field(default_factory=dict)
    category_summary: dict[str, dict[str, Any]] = field(default_factory=dict)
    # Detections of families outside the kit (unassigned, counted in the total, human review).
    out_of_kit: list[dict[str, Any]] = field(default_factory=list)
    model_version: str = "yolo26l-demo"
    conf_threshold: float = CONF_DISPLAY_THRESHOLD
    frame_index: int = 0
    frames_processed: int = 0
    cancel: bool = False
    last_error: str | None = None
    pipeline: str = WORKER_PIPELINE
    assign_solver: str = ASSIGN_SOLVER
    run_id: str | None = None
    hold_seconds: float = HOLD_SECONDS
    name_hold_seconds: float = NAME_HOLD_SECONDS
    hold_min_score: float = HOLD_MIN_SCORE
    hold_absent_seconds: float = HOLD_ABSENT_SECONDS
    hold_absent_streak: int = HOLD_ABSENT_STREAK
    hold_seed_frames: int = HOLD_SEED_FRAMES
    hold_zone_iou: float = HOLD_ZONE_IOU
    count_sample_every: int = COUNT_SAMPLE_EVERY
    timeline_samples: int = 0
    timeline_path: str | None = None
    # One uploaded video per session, used for both edge counts (first / last valid frame).
    video_id: str | None = None
    video_path: str | None = None
    video_meta: dict[str, Any] = field(default_factory=dict)
    # Single-frame analyses of the edge frames: never touched by run_frame (intermediate frames).
    initial_frame: dict[str, Any] | None = None
    final_frame: dict[str, Any] | None = None
    error_stage: str | None = None  # first_frame | processing | last_frame
    # family_code -> hold snapshot
    _hold_by_family: dict[str, dict[str, Any]] = field(default_factory=dict)
    # family_code -> consecutive live / absent processed-frame streaks
    _live_streak: dict[str, int] = field(default_factory=dict)
    _absent_streak: dict[str, int] = field(default_factory=dict)

    def to_public(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "status": self.status,
            "message": self.message,
            "progress": self.progress,
            "frame_jpeg_b64": self.frame_jpeg_b64,
            "boxes": self.boxes,
            "counts": self.counts,
            "raw_counts": self.raw_counts,
            "assignment_metrics": self.assignment_metrics,
            "category_summary": self.category_summary,
            "out_of_kit": self.out_of_kit,
            "pipeline": self.pipeline,
            "assign_solver": self.assign_solver,
            "model_version": self.model_version,
            "conf_threshold": self.conf_threshold,
            "frame_index": self.frame_index,
            "frames_processed": self.frames_processed,
            "last_error": self.last_error,
            "run_id": self.run_id,
            "hold_seconds": self.hold_seconds,
            "name_hold_seconds": self.name_hold_seconds,
            "hold_absent_seconds": self.hold_absent_seconds,
            "hold_absent_streak": self.hold_absent_streak,
            "hold_seed_frames": self.hold_seed_frames,
            "count_sample_every": self.count_sample_every,
            "timeline_samples": self.timeline_samples,
            "timeline_path": self.timeline_path,
            "video_id": self.video_id,
            "video": dict(self.video_meta) if self.video_id else None,
            "initial_frame": self.initial_frame,
            "final_frame": self.final_frame,
            "error_stage": self.error_stage,
            "expected": {
                code: {
                    "family_id": meta.get("family_id"),
                    "family_name": meta.get("family_name"),
                    "expected_quantity": meta.get("expected_quantity"),
                    "category_name": meta.get("category_name"),
                }
                for code, meta in self.expected.items()
            },
        }


def _draw_boxes(frame_bgr: np.ndarray, boxes: list[dict[str, Any]]) -> np.ndarray:
    out = frame_bgr.copy()
    for box in boxes:
        x1, y1, x2, y2 = [int(v) for v in box["xyxy"]]
        label = box.get("label") or box.get("family_code") or "?"
        conf = float(box.get("confidence") or 0.0)
        color = overlay_bgr(
            category=box.get("category_name"),
            family_code=box.get("family_code"),
            high_confidence=bool(box.get("high_confidence")),
            sticky_name=bool(box.get("sticky_name")),
            resolution_reason=box.get("resolution_reason"),
        )
        cv2.rectangle(out, (x1, y1), (x2, y2), color, 2)
        text = f"{label} {conf:.2f}"
        (tw, th), _ = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, 0.55, 1)
        cv2.rectangle(out, (x1, max(0, y1 - th - 8)), (x1 + tw + 6, y1), color, -1)
        # Dark text on saturated fills; light text on soft/slate fills for contrast.
        avg = (color[0] + color[1] + color[2]) / 3.0
        text_color = (15, 23, 42) if avg > 140 else (248, 250, 252)
        cv2.putText(
            out, text, (x1 + 3, y1 - 4),
            cv2.FONT_HERSHEY_SIMPLEX, 0.55, text_color, 1, cv2.LINE_AA,
        )
    return out


def _encode_jpeg_b64(frame_bgr: np.ndarray) -> str:
    ok, buf = cv2.imencode(".jpg", frame_bgr, [int(cv2.IMWRITE_JPEG_QUALITY), 82])
    if not ok:
        raise RuntimeError("Failed to encode JPEG frame")
    return base64.b64encode(buf.tobytes()).decode("ascii")


def _raw_counts_from_boxes(
    raw_boxes: list[dict[str, Any]],
    *,
    expected: dict[str, dict[str, Any]],
    class_map: dict[int, dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    tallies: dict[str, list[float]] = {code: [] for code in expected}
    for box in raw_boxes:
        class_id = int(box["class_id"])
        meta = class_map.get(class_id)
        if meta is None:
            continue
        code = meta.get("family_code")
        if code not in tallies:
            continue
        tallies[code].append(float(box["confidence"]))
    out: dict[str, dict[str, Any]] = {}
    for code, exp in expected.items():
        confs = tallies.get(code) or []
        n = len(confs)
        eq = int(exp.get("expected_quantity") or 0)
        out[code] = {
            "family_id": exp.get("family_id"),
            "family_code": code,
            "family_name": exp.get("family_name"),
            "category_name": exp.get("category_name"),
            "expected_quantity": eq,
            "detected_quantity": n,
            "difference": n - eq,
            "avg_confidence": (sum(confs) / n) if n else None,
            "best_confidence": max(confs) if confs else None,
            "weakest_confidence": min(confs) if confs else None,
        }
    return out


def _enrich_class_map_from_names(raw: list[dict[str, Any]], state: SessionState) -> None:
    name_to_meta = {
        (meta.get("family_name") or "").strip().lower(): meta
        for meta in state.class_map.values()
    }
    for box in raw:
        if int(box["class_id"]) in state.class_map:
            continue
        meta = name_to_meta.get(str(box.get("model_name") or "").strip().lower())
        if meta:
            state.class_map[int(box["class_id"])] = meta


def _public_box(box: dict[str, Any], *, held: bool = False, sticky_name: bool = False) -> dict[str, Any]:
    return {
        "xyxy": box["xyxy"],
        "label": box.get("label"),
        "confidence": box.get("confidence"),
        "high_confidence": box.get("high_confidence"),
        "family_code": box.get("family_code"),
        "family_id": box.get("family_id"),
        "family_name": box.get("family_name"),
        "category_name": box.get("category_name"),
        "resolution_reason": box.get("resolution_reason"),
        "raw": box.get("raw"),
        "refined": box.get("refined"),
        "audit": box.get("audit"),
        "out_of_kit": box.get("out_of_kit"),
        "held": held,
        "sticky_name": sticky_name,
    }


def _box_score(box: dict[str, Any]) -> float:
    return float(box.get("confidence") or 0.0)


def _family_live_stats(boxes: list[dict[str, Any]], code: str) -> tuple[float, bool]:
    fam = [b for b in boxes if b.get("family_code") == code]
    if not fam:
        return 0.0, False
    best = max(_box_score(b) for b in fam)
    named = any(bool(b.get("high_confidence")) for b in fam)
    return best, named


def _xyxy_iou(a: list[float] | tuple[float, ...], b: list[float] | tuple[float, ...]) -> float:
    try:
        ax1, ay1, ax2, ay2 = [float(v) for v in a[:4]]
        bx1, by1, bx2, by2 = [float(v) for v in b[:4]]
    except (TypeError, ValueError, IndexError):
        return 0.0
    ix1, iy1 = max(ax1, bx1), max(ay1, by1)
    ix2, iy2 = min(ax2, bx2), min(ay2, by2)
    iw, ih = max(0.0, ix2 - ix1), max(0.0, iy2 - iy1)
    inter = iw * ih
    if inter <= 0:
        return 0.0
    area_a = max(0.0, ax2 - ax1) * max(0.0, ay2 - ay1)
    area_b = max(0.0, bx2 - bx1) * max(0.0, by2 - by1)
    denom = area_a + area_b - inter
    return inter / denom if denom > 0 else 0.0


def _hold_zone_occluded(
    hold: dict[str, Any],
    live_boxes: list[dict[str, Any]],
    *,
    min_iou: float,
) -> bool:
    """True if any live box (named or type) still overlaps last hold geometry."""
    hold_boxes = hold.get("boxes") or []
    if not hold_boxes or not live_boxes:
        return False
    for hb in hold_boxes:
        hxy = hb.get("xyxy")
        if not hxy:
            continue
        for lb in live_boxes:
            lxy = lb.get("xyxy")
            if not lxy:
                continue
            if _xyxy_iou(hxy, lxy) >= min_iou:
                return True
    return False


def _apply_sticky_name(box: dict[str, Any], hold: dict[str, Any], video_t: float) -> dict[str, Any]:
    """If hold recently had a named family, keep family label despite live category degrade."""
    if not hold:
        return box
    if video_t > float(hold.get("name_until") or 0):
        return box
    if not hold.get("named"):
        return box
    if not box.get("family_code"):
        return box
    if box.get("high_confidence"):
        return box
    family_name = box.get("family_name") or hold.get("family_name")
    if not family_name:
        return box
    out = copy.deepcopy(box)
    out["label"] = family_name
    out["sticky_name"] = True
    return out


def _should_refresh_hold(
    *,
    prev: dict[str, Any] | None,
    live_best: float,
    live_named: bool,
    min_score: float,
) -> bool:
    if live_best < min_score and not live_named:
        return False
    if prev is None:
        return live_best >= min_score or live_named
    prev_best = float(prev.get("best_score") or 0.0)
    prev_named = bool(prev.get("named"))
    if prev_named and not live_named and live_best < prev_best - 0.02:
        return False
    if live_named:
        return True
    return live_best + 0.02 >= prev_best


def _apply_hold(
    state: SessionState,
    *,
    counts: dict[str, dict[str, Any]],
    public_boxes: list[dict[str, Any]],
    video_t: float,
) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]]]:
    """
    Count-hold + sticky naming with production-minded gates:
    - Seed hold only after HOLD_SEED_FRAMES consecutive live assignments.
    - Long TTL while live or while a box still overlaps hold zone (hand/occlusion).
    - After HOLD_ABSENT_STREAK frames at 0 live without zone overlap, shrink to HOLD_ABSENT_SECONDS.
    """
    ttl = max(0.0, float(state.hold_seconds))
    name_ttl = max(ttl, float(state.name_hold_seconds))
    absent_ttl = max(0.05, float(state.hold_absent_seconds))
    min_score = float(state.hold_min_score)
    seed_need = max(1, int(state.hold_seed_frames))
    absent_need = max(1, int(state.hold_absent_streak))
    zone_iou = float(state.hold_zone_iou)

    live_codes = {
        code for code, meta in counts.items()
        if int(meta.get("detected_quantity") or 0) > 0
    }

    for code in list(state.expected.keys()):
        if code in live_codes:
            state._live_streak[code] = int(state._live_streak.get(code) or 0) + 1
            state._absent_streak[code] = 0
        else:
            state._absent_streak[code] = int(state._absent_streak.get(code) or 0) + 1
            state._live_streak[code] = 0

    for code in live_codes:
        fam_boxes = [b for b in public_boxes if b.get("family_code") == code]
        live_best, live_named = _family_live_stats(public_boxes, code)
        prev = state._hold_by_family.get(code)
        live_streak = int(state._live_streak.get(code) or 0)
        seeded = prev is not None or live_streak >= seed_need
        if not seeded:
            continue
        if _should_refresh_hold(
            prev=prev, live_best=live_best, live_named=live_named, min_score=min_score
        ):
            named = live_named or bool(
                prev and prev.get("named") and video_t <= float(prev.get("name_until") or 0)
            )
            store_boxes = copy.deepcopy(fam_boxes)
            if named and not live_named:
                for b in store_boxes:
                    b["label"] = b.get("family_name") or b.get("label")
                    b["sticky_name"] = True
            state._hold_by_family[code] = {
                "until": video_t + ttl,
                "name_until": (
                    (video_t + name_ttl)
                    if named
                    else float((prev or {}).get("name_until") or 0)
                ),
                "count": copy.deepcopy(counts[code]),
                "boxes": store_boxes,
                "best_score": max(live_best, float((prev or {}).get("best_score") or 0.0)),
                "named": named,
                "family_name": (
                    counts[code].get("family_name") or (prev or {}).get("family_name")
                ),
            }
        elif prev is not None:
            if live_best >= min_score or live_named:
                prev["until"] = max(float(prev.get("until") or 0), video_t + ttl * 0.5)
            if live_named:
                prev["named"] = True
                prev["name_until"] = video_t + name_ttl
                prev["best_score"] = max(float(prev.get("best_score") or 0), live_best)

    # Absent path: grace streak, then short TTL unless zone still occupied.
    for code in list(state.expected.keys()):
        if code in live_codes:
            continue
        hold = state._hold_by_family.get(code)
        if not hold:
            continue
        absent_streak = int(state._absent_streak.get(code) or 0)
        if absent_streak < absent_need:
            continue
        if _hold_zone_occluded(hold, public_boxes, min_iou=zone_iou):
            # Something still in the tray zone → keep long occlusion budget.
            hold["until"] = max(float(hold.get("until") or 0), video_t + ttl * 0.5)
            continue
        # Confirmed gone: shrink remaining hold/name windows.
        hold["until"] = min(float(hold.get("until") or 0), video_t + absent_ttl)
        if hold.get("named"):
            hold["name_until"] = min(
                float(hold.get("name_until") or 0),
                video_t + max(absent_ttl, absent_ttl * 1.25),
            )

    sticky_boxes: list[dict[str, Any]] = []
    for box in public_boxes:
        code = box.get("family_code")
        hold = state._hold_by_family.get(code) if code else None
        sticky_boxes.append(_apply_sticky_name(box, hold or {}, video_t))

    out_counts = copy.deepcopy(counts)
    held_boxes: list[dict[str, Any]] = []
    for code in list(state.expected.keys()):
        det = int(out_counts.get(code, {}).get("detected_quantity") or 0)
        if det > 0:
            out_counts[code]["held"] = False
            hold = state._hold_by_family.get(code)
            if hold and hold.get("named") and video_t <= float(hold.get("name_until") or 0):
                out_counts[code]["sticky_name"] = True
            continue
        hold = state._hold_by_family.get(code)
        if not hold:
            continue
        if video_t > float(hold["until"]):
            if video_t > float(hold.get("name_until") or 0):
                state._hold_by_family.pop(code, None)
            continue
        if float(hold.get("best_score") or 0) < min_score and not hold.get("named"):
            continue
        restored = copy.deepcopy(hold["count"])
        restored["held"] = True
        restored["sticky_name"] = bool(hold.get("named"))
        restored["hold_remaining_s"] = round(float(hold["until"]) - video_t, 2)
        restored["raw_detected_quantity"] = counts.get(code, {}).get("raw_detected_quantity", 0)
        restored["difference"] = int(restored.get("detected_quantity") or 0) - int(
            restored.get("expected_quantity") or 0
        )
        out_counts[code] = restored
        for b in hold.get("boxes") or []:
            if any(_xyxy_iou(b.get("xyxy") or [], live.get("xyxy") or []) >= HOLD_DEDUPE_IOU
                   for live in sticky_boxes):
                # Same object, live under another label this frame: never a second instrument.
                continue
            hb = copy.deepcopy(b)
            hb["held"] = True
            if hold.get("named"):
                hb["label"] = hb.get("family_name") or hold.get("family_name") or hb.get("label")
                hb["sticky_name"] = True
            held_boxes.append(hb)

    return out_counts, sticky_boxes + held_boxes


def _out_of_kit_summary(boxes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Unassigned detections of families outside the kit, grouped by YOLO's family guess."""
    groups: dict[str, dict[str, Any]] = {}
    for box in boxes:
        if box.get("resolution_reason") != "out_of_kit":
            continue
        guess = box.get("out_of_kit") or {}
        key = str(guess.get("family_code") or "unknown")
        row = groups.setdefault(key, {
            "family_code": guess.get("family_code"),
            "family_id": guess.get("family_id"),
            "family_name": guess.get("family_name"),
            "category_name": box.get("category_name"),
            "count": 0,
            "best_confidence": None,
        })
        row["count"] += 1
        conf = box.get("confidence")
        if conf is not None and (row["best_confidence"] is None or conf > row["best_confidence"]):
            row["best_confidence"] = round(float(conf), 4)
    return sorted(groups.values(), key=lambda r: str(r.get("family_code") or ""))


def _category_summary(
    *,
    expected: dict[str, dict[str, Any]],
    counts: dict[str, dict[str, Any]],
    boxes: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    """Per instrument type: expected, named, unnamed (capped), pending families."""
    cats: dict[str, dict[str, Any]] = {}
    for code, exp in expected.items():
        cat = exp.get("category_name") or "Type"
        bucket = cats.setdefault(cat, {
            "category_name": cat,
            "expected": 0,
            "detected": 0,
            "named": 0,
            "unnamed_raw": 0,
            "unnamed": 0,
            "pending": [],
            "families": [],
        })
        eq = int(exp.get("expected_quantity") or 0)
        bucket["expected"] += eq
        bucket["families"].append(code)
        meta = counts.get(code) or {}
        det = int(meta.get("detected_quantity") or 0)
        bucket["detected"] += det
        if det < eq:
            bucket["pending"].append({
                "family_code": code,
                "family_name": exp.get("family_name") or code,
                "missing": eq - det,
            })

    for box in boxes:
        if box.get("resolution_reason") == "out_of_kit":
            continue  # not part of any kit quota (reported in out_of_kit)
        cat = box.get("category_name") or "Type"
        if cat not in cats:
            continue
        named = bool(box.get("high_confidence") or box.get("sticky_name")) and bool(box.get("family_code"))
        if named:
            cats[cat]["named"] += 1
        elif box.get("resolution_reason") == "type_degraded" or (
            box.get("family_code") and not named
        ) or (not box.get("family_code") and box.get("label")):
            # Category label / type degrade / assigned-but-unnamed
            if box.get("category_name") == cat or box.get("label") == cat:
                cats[cat]["unnamed_raw"] += 1
            elif box.get("family_code") and not named:
                cats[cat]["unnamed_raw"] += 1

    for cat, bucket in cats.items():
        room = max(0, int(bucket["expected"]) - int(bucket["named"]))
        bucket["unnamed"] = min(int(bucket["unnamed_raw"]), room)
        bucket["pending_count"] = sum(p["missing"] for p in bucket["pending"])
    return cats


def _metrics_payload(state: SessionState) -> dict[str, Any]:
    return {
        "session_id": state.session_id,
        "run_id": state.run_id,
        "pipeline": state.pipeline,
        "assign_solver": state.assign_solver,
        "model_version": state.model_version,
        "frame_index": state.frame_index,
        "frames_processed": state.frames_processed,
        "hold_seconds": state.hold_seconds,
        "name_hold_seconds": state.name_hold_seconds,
        "count_sample_every": state.count_sample_every,
        "timeline_samples": state.timeline_samples,
        "written_at": datetime.now(timezone.utc).isoformat(),
        "assignment_metrics": state.assignment_metrics,
        "category_summary": state.category_summary,
        "raw_counts": state.raw_counts,
        "refined_counts": {
            code: {
                "expected_quantity": meta.get("expected_quantity"),
                "detected_quantity": meta.get("detected_quantity"),
                "raw_detected_quantity": meta.get("raw_detected_quantity"),
                "difference": meta.get("difference"),
                "best_confidence": meta.get("best_confidence"),
                "weakest_confidence": meta.get("weakest_confidence"),
                "avg_confidence": meta.get("avg_confidence"),
                "reassigned_in": meta.get("reassigned_in"),
                "reassigned_out": meta.get("reassigned_out"),
                "held": meta.get("held"),
                "sticky_name": meta.get("sticky_name"),
                "boxes": meta.get("boxes") or [],
            }
            for code, meta in (state.counts or {}).items()
        },
    }


def _write_metrics_snapshot(state: SessionState, *, final: bool = False) -> None:
    try:
        METRICS_DIR.mkdir(parents=True, exist_ok=True)
        payload = _metrics_payload(state)
        (METRICS_DIR / f"{state.session_id}_last.json").write_text(
            json.dumps(payload, indent=2), encoding="utf-8"
        )
        if final and state.run_id:
            (METRICS_DIR / f"{state.session_id}_{state.run_id}.json").write_text(
                json.dumps(payload, indent=2), encoding="utf-8"
            )
    except OSError:
        pass


def _compact_metrics(metrics: dict[str, Any] | None) -> dict[str, Any]:
    """Drop heavy type_degraded_boxes from sampled timeline / window payloads."""
    if not metrics:
        return {}
    skip = {"type_degraded_boxes"}
    return {k: v for k, v in metrics.items() if k not in skip}


def _compact_box_for_timeline(box: dict[str, Any]) -> dict[str, Any]:
    """Observable box audit only (raw top-1, assigned, top-k); no matrix internals."""
    if "yolo_top1" in box or "assigned" in box:
        topk = box.get("topk") or []
        return {
            "xyxy": box.get("xyxy"),
            "yolo_top1": box.get("yolo_top1"),
            "assigned": box.get("assigned"),
            "topk": topk[:4] if isinstance(topk, list) else [],
        }
    audit = box.get("audit") or {}
    if audit:
        topk = audit.get("topk") or []
        return {
            "xyxy": audit.get("xyxy") or box.get("xyxy"),
            "yolo_top1": audit.get("yolo_top1"),
            "assigned": audit.get("assigned"),
            "topk": topk[:4] if isinstance(topk, list) else [],
        }
    return {
        "xyxy": box.get("xyxy"),
        "yolo_top1": {
            "family_code": box.get("family_code"),
            "score": box.get("confidence"),
        },
        "assigned": {
            "family_code": box.get("family_code"),
            "score": box.get("confidence"),
            "reason": box.get("resolution_reason"),
        },
        "topk": [],
    }


def _compact_counts_for_timeline(counts: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for code, meta in (counts or {}).items():
        boxes = meta.get("boxes") or []
        out[code] = {
            "family_id": meta.get("family_id"),
            "family_code": meta.get("family_code") or code,
            "family_name": meta.get("family_name"),
            "category_name": meta.get("category_name"),
            "expected_quantity": meta.get("expected_quantity"),
            "detected_quantity": meta.get("detected_quantity"),
            "raw_detected_quantity": meta.get("raw_detected_quantity"),
            "difference": meta.get("difference"),
            "best_confidence": meta.get("best_confidence"),
            "weakest_confidence": meta.get("weakest_confidence"),
            "avg_confidence": meta.get("avg_confidence"),
            "reassigned_in": meta.get("reassigned_in"),
            "reassigned_out": meta.get("reassigned_out"),
            "held": bool(meta.get("held")),
            "sticky_name": meta.get("sticky_name"),
            "box_count": len(boxes),
            # Compact observables for board backfill / DS06 bridge (not the score matrix).
            "boxes": [
                _compact_box_for_timeline(b) for b in boxes if isinstance(b, dict)
            ],
            "pipeline": meta.get("pipeline") or WORKER_PIPELINE,
        }
    return out


def _timeline_path_for(state: SessionState):
    if not state.run_id:
        return None
    return METRICS_DIR / f"{state.session_id}_{state.run_id}_timeline.ndjson"


def _should_sample_timeline(state: SessionState, *, final: bool = False) -> bool:
    if state.frames_processed <= 0:
        return False
    every = max(1, int(state.count_sample_every or COUNT_SAMPLE_EVERY))
    on_boundary = state.frames_processed % every == 0
    if final:
        # run_frame already wrote the boundary sample; only fill the gap to last frame.
        return not on_boundary
    return on_boundary


def _append_timeline_sample(
    state: SessionState,
    *,
    video_t: float = 0.0,
    final: bool = False,
) -> None:
    if not _should_sample_timeline(state, final=final):
        return
    path = _timeline_path_for(state)
    if path is None:
        return
    sample = {
        "session_id": state.session_id,
        "run_id": state.run_id,
        "frame_index": state.frame_index,
        "frames_processed": state.frames_processed,
        "video_t": round(float(video_t), 3),
        "progress": state.progress,
        "final": bool(final),
        "pipeline": state.pipeline,
        "assign_solver": state.assign_solver,
        "model_version": state.model_version,
        "conf_threshold": state.conf_threshold,
        "count_sample_every": state.count_sample_every,
        "written_at": datetime.now(timezone.utc).isoformat(),
        "assignment_metrics": _compact_metrics(state.assignment_metrics),
        "counts": _compact_counts_for_timeline(state.counts),
        # Windows stay lean in PG by default; final board flush can include boxes.
        "include_boxes": bool(final),
    }
    try:
        METRICS_DIR.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(sample, separators=(",", ":")) + "\n")
        state.timeline_path = str(path.name)
        state.timeline_samples = int(state.timeline_samples or 0) + 1
    except OSError:
        pass


def read_timeline_samples(session_id: str, run_id: str | None = None) -> list[dict[str, Any]]:
    """Load NDJSON timeline for a session run (default: latest matching file)."""
    METRICS_DIR.mkdir(parents=True, exist_ok=True)
    path = None
    if run_id:
        candidate = METRICS_DIR / f"{session_id}_{run_id}_timeline.ndjson"
        if candidate.is_file():
            path = candidate
    if path is None:
        matches = sorted(
            METRICS_DIR.glob(f"{session_id}_*_timeline.ndjson"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        path = matches[0] if matches else None
    if path is None or not path.is_file():
        return []
    samples: list[dict[str, Any]] = []
    try:
        with path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    samples.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except OSError:
        return []
    return samples


def run_frame(
    frame_bgr: np.ndarray,
    state: SessionState,
    *,
    video_t: float = 0.0,
) -> None:
    model = get_model()
    raw = detect_with_class_scores(model, frame_bgr)
    _enrich_class_map_from_names(raw, state)

    refined, counts, metrics = assign_boxes(
        raw,
        expected=state.expected,
        class_map=state.class_map,
        conf_display=state.conf_threshold,
    )
    public_boxes = [_public_box(box) for box in refined]
    held_counts, held_boxes = _apply_hold(
        state, counts=counts, public_boxes=public_boxes, video_t=video_t
    )

    state.boxes = held_boxes
    state.counts = held_counts
    state.raw_counts = _raw_counts_from_boxes(
        raw, expected=state.expected, class_map=state.class_map
    )
    state.assignment_metrics = metrics
    state.category_summary = _category_summary(
        expected=state.expected, counts=held_counts, boxes=held_boxes
    )
    state.out_of_kit = _out_of_kit_summary(held_boxes)
    state.pipeline = WORKER_PIPELINE
    state.assign_solver = str((metrics or {}).get("solver") or ASSIGN_SOLVER)
    annotated = _draw_boxes(frame_bgr, held_boxes)
    state.frame_jpeg_b64 = _encode_jpeg_b64(annotated)
    state.frames_processed += 1
    _write_metrics_snapshot(state)
    _append_timeline_sample(state, video_t=video_t, final=False)


def frame_quality(frame_bgr: np.ndarray | None) -> dict[str, Any]:
    """Whether a decoded frame can show the tray: not empty, not black, not washed out or uniform.

    Cheap global statistics only; it rejects frames no count may come from (fade-in/out, lens cap,
    blank encoder frames). Whether the tray itself is in view is checked by the Operator.
    """
    if frame_bgr is None or not getattr(frame_bgr, "size", 0):
        return {"ok": False, "reason": "not_decodable"}
    gray = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY) if frame_bgr.ndim == 3 else frame_bgr
    mean, std = cv2.meanStdDev(gray)
    brightness, contrast = float(mean[0][0]), float(std[0][0])
    metrics = {"brightness": round(brightness, 2), "contrast": round(contrast, 2)}
    if brightness < FRAME_MIN_BRIGHTNESS:
        return {"ok": False, "reason": "too_dark", **metrics}
    if brightness > FRAME_MAX_BRIGHTNESS:
        return {"ok": False, "reason": "overexposed", **metrics}
    if contrast < FRAME_MIN_CONTRAST:
        return {"ok": False, "reason": "uniform", **metrics}
    return {"ok": True, "reason": None, **metrics}


def analyze_single_frame(
    frame_bgr: np.ndarray,
    state: SessionState,
    *,
    frame_index: int,
    video_t: float,
    position: str,
) -> dict[str, Any]:
    """YOLO + matrix_v1 on ONE frame, without the video hold: the count of that frame only.

    Used for the edge frames (position first | last). It never reads or writes the live run
    state (counts, boxes, holds), so intermediate frames cannot change it.
    """
    model = get_model()
    raw = detect_with_class_scores(model, frame_bgr)
    _enrich_class_map_from_names(raw, state)
    refined, counts, metrics = assign_boxes(
        raw,
        expected=state.expected,
        class_map=state.class_map,
        conf_display=state.conf_threshold,
    )
    boxes = [_public_box(box) for box in refined]
    return {
        "ok": True,
        "position": position,
        "video_id": state.video_id,
        "run_id": state.run_id if position == "last" else None,
        "frame_index": int(frame_index),
        "video_t": round(float(video_t), 3),
        "analyzed_at": datetime.now(timezone.utc).isoformat(),
        "model_version": state.model_version,
        "pipeline": WORKER_PIPELINE,
        "assign_solver": str((metrics or {}).get("solver") or ASSIGN_SOLVER),
        "conf_threshold": state.conf_threshold,
        "quality": frame_quality(frame_bgr),
        "boxes": boxes,
        "total": len(boxes),
        "counts": counts,
        "raw_counts": _raw_counts_from_boxes(raw, expected=state.expected, class_map=state.class_map),
        "assignment_metrics": metrics,
        "category_summary": _category_summary(expected=state.expected, counts=counts, boxes=boxes),
        "out_of_kit": _out_of_kit_summary(boxes),
        "frame_jpeg_b64": _encode_jpeg_b64(_draw_boxes(frame_bgr, boxes)),
    }


def _edge_failure(position: str, reason: str, detail: str, **extra: Any) -> dict[str, Any]:
    return {"ok": False, "position": position, "reason": reason, "error": detail, **extra}


def analyze_first_frame(path: str, state: SessionState) -> None:
    """Upload step: analyse frame zero only; never starts the full run or skips a bad first frame."""
    state.status = "loading"
    state.message = "Analysing the first frame"
    state.error_stage = None
    state.last_error = None
    cap = cv2.VideoCapture(path)
    try:
        if not cap.isOpened():
            raise _EdgeError(_edge_failure("first", "not_decodable", "Could not open video file"))
        fps = float(cap.get(cv2.CAP_PROP_FPS) or 0.0) or 30.0
        state.video_meta.update({
            "frame_count": int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0),
            "fps": round(fps, 3),
            "width": int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0),
            "height": int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0),
        })
        if state.video_meta["frame_count"]:
            state.video_meta["duration_s"] = round(state.video_meta["frame_count"] / fps, 3)
        rejected = []
        # The initial count belongs to frame zero. A fade-in is an error to review,
        # never permission to silently choose a later tray configuration.
        for idx in range(1):
            ok, frame = cap.read()
            if not ok:
                break
            quality = frame_quality(frame)
            if not quality["ok"]:
                rejected.append({"frame_index": idx, **quality})
                continue
            result = analyze_single_frame(frame, state, frame_index=idx, video_t=idx / fps, position="first")
            result["skipped_frames"] = rejected
            state.initial_frame = result
            state.frame_jpeg_b64 = result["frame_jpeg_b64"]
            state.status = "awaiting_initial"
            state.message = "First frame analysed; waiting for the initial count"
            return
        if not rejected:
            raise _EdgeError(_edge_failure("first", "no_frames", "The video has no decodable frame"))
        raise _EdgeError(_edge_failure("first", "no_valid_frame",
                                       f"No usable frame in the first {len(rejected)} frames", skipped_frames=rejected))
    except _EdgeError as exc:
        state.initial_frame = exc.result
        state.status = "error"
        state.error_stage = "first_frame"
        state.last_error = exc.result["error"]
        state.message = state.last_error
    except Exception as exc:  # noqa: BLE001 — surface to UI, never a fictitious count
        state.initial_frame = _edge_failure("first", "inference_error", f"Inference error: {exc}")
        state.status = "error"
        state.error_stage = "first_frame"
        state.last_error = str(exc)
        state.message = f"Inference error: {exc}"
    finally:
        cap.release()


class _EdgeError(Exception):
    def __init__(self, result: dict[str, Any]):
        super().__init__(result.get("error"))
        self.result = result


def _analyze_last_frame(state: SessionState, tail: deque, fps: float) -> dict[str, Any]:
    """Last valid frame of the run: the newest decoded frame of the tail that passes the quality gate."""
    rejected = []
    for idx, frame in reversed(tail):
        quality = frame_quality(frame)
        if quality["ok"]:
            result = analyze_single_frame(frame, state, frame_index=idx, video_t=idx / fps, position="last")
            result["skipped_frames"] = rejected
            return result
        rejected.append({"frame_index": idx, **quality})
    if not rejected:
        return _edge_failure("last", "no_frames", "The video has no decodable frame")
    return _edge_failure("last", "no_valid_frame",
                         f"No usable frame in the last {len(rejected)} frames", skipped_frames=rejected)


def process_video_file(path: str, state: SessionState) -> None:
    state.status = "processing"
    state.message = "Processing video (matrix_v1)"
    state.progress = 0.0
    state.cancel = False
    state.last_error = None
    state.run_id = uuid.uuid4().hex[:12]
    state._hold_by_family = {}
    state._live_streak = {}
    state._absent_streak = {}
    state.frames_processed = 0
    state.timeline_samples = 0
    state.timeline_path = None
    state.count_sample_every = COUNT_SAMPLE_EVERY
    state.hold_seconds = HOLD_SECONDS
    state.name_hold_seconds = NAME_HOLD_SECONDS
    state.hold_min_score = HOLD_MIN_SCORE
    state.hold_absent_seconds = HOLD_ABSENT_SECONDS
    state.hold_absent_streak = HOLD_ABSENT_STREAK
    state.hold_seed_frames = HOLD_SEED_FRAMES
    state.hold_zone_iou = HOLD_ZONE_IOU
    state.category_summary = {}
    # A new run never shows the tallies or boxes of an earlier one (e.g. after an error).
    state.counts = {}
    state.raw_counts = {}
    state.boxes = []
    state.out_of_kit = []
    state.assignment_metrics = {}
    state.frame_jpeg_b64 = None
    state.frame_index = 0
    state.final_frame = None
    state.error_stage = None
    # Truncate prior timeline for this new run id (file is new per run_id).
    cap = cv2.VideoCapture(path)
    if not cap.isOpened():
        state.status = "error"
        state.error_stage = "processing"
        state.last_error = "Could not open video file"
        state.message = state.last_error
        return
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    fps = float(cap.get(cv2.CAP_PROP_FPS) or 0.0) or 30.0
    idx = 0
    last_video_t = 0.0
    # Newest decoded frames (references, no copies): the last valid one is the final count.
    tail: deque = deque(maxlen=EDGE_FRAME_SCAN)
    # DS06 session_telemetry, measured on this run: one sample per COUNT_SAMPLE_EVERY analysed
    # frames (+ the remainder at the end). fps_avg = analysed frames per wall-clock second,
    # latency_ms = mean run_frame time, drops = frames read but not analysed (FRAME_STRIDE).
    window = {"start": time.perf_counter(), "frames": 0, "latency": 0.0, "drops": 0}

    def _emit_window() -> None:
        if not window["frames"]:
            return
        elapsed = max(time.perf_counter() - window["start"], 1e-6)
        TELEMETRY.emit(session_id=state.session_id, ts=utc_now(),
                       fps_avg=round(window["frames"] / elapsed, 3),
                       latency_ms=round(window["latency"] / window["frames"] * 1000.0, 3),
                       drops=int(window["drops"]))
        window.update(start=time.perf_counter(), frames=0, latency=0.0, drops=0)

    try:
        while True:
            if state.cancel:
                state.status = "idle"
                state.message = "Cancelled"
                break
            ok, frame = cap.read()
            if not ok:
                break
            tail.append((idx, frame))
            state.frame_index = idx
            last_video_t = idx / fps
            if idx % FRAME_STRIDE == 0:
                started = time.perf_counter()
                run_frame(frame, state, video_t=last_video_t)
                window["latency"] += time.perf_counter() - started
                window["frames"] += 1
                if window["frames"] >= max(1, COUNT_SAMPLE_EVERY):
                    _emit_window()
            else:
                window["drops"] += 1
            if total > 0:
                state.progress = min(0.99, (idx + 1) / total)
            idx += 1
        _emit_window()
        if state.status == "processing":
            if total > 0 and idx < total - 1:
                raise RuntimeError("Video decoding ended before the declared final frames; "
                                   "no final count was generated. Check the video file.")
            _write_metrics_snapshot(state, final=True)
            # Ensure final tally is on the timeline even if not on a sample boundary.
            _append_timeline_sample(state, video_t=last_video_t, final=True)
            # The run only becomes ready once the last valid frame has its own analysis.
            state.message = "Analysing the last frame"
            try:
                final = _analyze_last_frame(state, tail, fps)
            except Exception as exc:  # noqa: BLE001 — never a fictitious final count
                final = _edge_failure("last", "inference_error", f"Inference error: {exc}")
            state.final_frame = final
            if final.get("ok"):
                state.progress = 1.0
                state.status = "ready"
                state.message = "Video processing complete"
            else:
                state.status = "error"
                state.error_stage = "last_frame"
                state.last_error = final.get("error")
                state.message = state.last_error
    except Exception as exc:  # noqa: BLE001 — surface to UI
        state.status = "error"
        state.error_stage = state.error_stage or "processing"
        state.last_error = str(exc)
        state.message = f"Inference error: {exc}"
    finally:
        cap.release()
