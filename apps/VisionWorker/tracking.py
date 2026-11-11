"""MS-11 (R11): temporal tracking of instruments between processed frames of ONE run (video or live preview).

What it is for — and what it is NOT for:
  * It gives every physical object a STABLE temporary identifier (track_id) across frames, smooths its
    classification over time, follows it through brief occlusions and records traceable events
    (new / confirmed / lost / recovered / terminated / class_change / enter_roi / exit_roi).
  * It NEVER produces or changes a count. The counts of PEF stay what MS-03.1 approved: the initial count is
    the first valid frame, the final count is the last valid frame, and the final count must equal the
    current reference. Track statistics (unique tracks, concurrent tracks…) are reported for traceability
    only and are never added up into a quantity: re-using or fragmenting an ID can therefore never inflate
    or deflate a count.

Algorithm (replaces the unused IoU+EMA helper of tracks.py, which re-numbered tracks on every frame):
  1. Prediction: constant-velocity motion of each track (velocity smoothed with an EMA), scaled by the time
     elapsed since the track was last seen.
  2. Association: Hungarian assignment (scipy) on a gated cost = 1 - (IoU with the predicted box
     + centre proximity bonus). A pair is only allowed when IoU >= min_iou OR the centre distance is within
     `center_gate` x the predicted box diagonal (fast motion without overlap) AND, when both have a family,
     the classes agree or the detection is low-confidence (prevents swapping two neighbouring instruments).
  3. Confidence: detections below `min_create_conf` may update an existing track but never create one
     (spurious low-confidence boxes do not become objects). A track is `confirmed` after `min_hits` hits.
  4. Occlusion: an unmatched confirmed track becomes `lost` after `lost_after_s` and is kept for
     `forget_after_s`; a detection matching it in that window RECOVERS the same ID (event `recovered`).
     Lost tracks are matched with a wider gate (no velocity extrapolation beyond `max_extrapolate_s`).
  5. Classification: per-track exponential vote of the assigned family (`ema_alpha`); the stable family only
     changes when another family's vote exceeds it by `class_switch_margin` (event `class_change`).
  6. ROI: if the station camera defines a region of interest, the box centre entering / leaving it produces
     `enter_roi` / `exit_roi` events. The ROI is NOT used to filter detections (that would change counts).

Every parameter is configurable (VISION_TRACK_*) and reported in the summary, so a run is reproducible.
"""
from __future__ import annotations

import math
import os
from dataclasses import dataclass, field
from typing import Any

try:
    from scipy.optimize import linear_sum_assignment
except Exception:  # pragma: no cover - scipy is in requirements; greedy fallback keeps the worker alive
    linear_sum_assignment = None

TRACKING_VERSION = "ms11-hungarian-cv-1"


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except ValueError:
        return default


@dataclass(frozen=True)
class TrackerConfig:
    min_iou: float = _env_float("VISION_TRACK_MIN_IOU", 0.20)
    center_gate: float = _env_float("VISION_TRACK_CENTER_GATE", 0.60)        # x predicted box diagonal
    lost_center_gate: float = _env_float("VISION_TRACK_LOST_CENTER_GATE", 1.00)
    # A track seen once has no velocity yet: a wider gate lets a moving instrument be picked up again on the
    # next processed frame (Hungarian assignment + class check keep neighbours apart).
    new_center_gate: float = _env_float("VISION_TRACK_NEW_CENTER_GATE", 1.50)
    min_hits: int = int(_env_float("VISION_TRACK_MIN_HITS", 3))
    # Default = the pipeline's own detection threshold (VISION_BOX_RECALL_CONF): the tracker follows exactly the
    # detections the pipeline accepts. A higher value made real low-confidence boxes untracked, so the active
    # tracks never matched the final frame (measured on the MS-03.1 videos, MS-11).
    min_create_conf: float = _env_float("VISION_TRACK_MIN_CREATE_CONF", _env_float("VISION_BOX_RECALL_CONF", 0.15))
    lost_after_s: float = _env_float("VISION_TRACK_LOST_AFTER_S", 0.40)
    forget_after_s: float = _env_float("VISION_TRACK_FORGET_AFTER_S", 3.0)
    max_extrapolate_s: float = _env_float("VISION_TRACK_MAX_EXTRAPOLATE_S", 0.50)
    velocity_alpha: float = _env_float("VISION_TRACK_VELOCITY_ALPHA", 0.50)
    ema_alpha: float = _env_float("VISION_TRACK_EMA_ALPHA", 0.35)
    class_switch_margin: float = _env_float("VISION_TRACK_CLASS_SWITCH_MARGIN", 0.15)
    max_events: int = int(_env_float("VISION_TRACK_MAX_EVENTS", 500))

    def public(self) -> dict[str, Any]:
        return {k: getattr(self, k) for k in self.__dataclass_fields__}


@dataclass
class Track:
    id: int
    xyxy: list[float]
    first_t: float
    last_t: float
    first_frame: int
    last_frame: int
    velocity: tuple[float, float] = (0.0, 0.0)
    hits: int = 1
    state: str = "tentative"            # tentative | confirmed | lost
    family: str | None = None
    votes: dict[str, float] = field(default_factory=dict)
    conf_ema: float = 0.0
    max_conf: float = 0.0
    in_roi: bool | None = None
    recoveries: int = 0
    class_changes: int = 0

    def predicted(self, t: float, max_extrapolate_s: float) -> list[float]:
        dt = max(0.0, min(t - self.last_t, max_extrapolate_s))
        dx, dy = self.velocity[0] * dt, self.velocity[1] * dt
        x1, y1, x2, y2 = self.xyxy
        return [x1 + dx, y1 + dy, x2 + dx, y2 + dy]


def iou(a, b) -> float:
    ix1, iy1 = max(a[0], b[0]), max(a[1], b[1])
    ix2, iy2 = min(a[2], b[2]), min(a[3], b[3])
    inter = max(0.0, ix2 - ix1) * max(0.0, iy2 - iy1)
    if inter <= 0:
        return 0.0
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / union if union > 0 else 0.0


def _center(b) -> tuple[float, float]:
    return (b[0] + b[2]) / 2.0, (b[1] + b[3]) / 2.0


def _diag(b) -> float:
    return max(1e-6, math.hypot(b[2] - b[0], b[3] - b[1]))


def normalize_roi(roi: dict[str, Any] | None, frame_w: int, frame_h: int) -> tuple[float, float, float, float] | None:
    """Camera ROI from Administration ({x, y, w, h, frame_w, frame_h} in pixels of the configured resolution)
    scaled to the size of the frames actually analysed. None when absent or incomplete."""
    if not isinstance(roi, dict) or not frame_w or not frame_h:
        return None
    try:
        x, y, w, h = (float(roi[k]) for k in ("x", "y", "w", "h"))
        fw, fh = float(roi.get("frame_w") or frame_w), float(roi.get("frame_h") or frame_h)
    except (KeyError, TypeError, ValueError):
        return None
    if w <= 0 or h <= 0 or fw <= 0 or fh <= 0:
        return None
    sx, sy = frame_w / fw, frame_h / fh
    return (x * sx, y * sy, (x + w) * sx, (y + h) * sy)


class InstrumentTracker:
    """One tracker per run; not thread-safe (the worker processes one run per session at a time)."""

    def __init__(self, config: TrackerConfig | None = None, *, roi=None):
        self.config = config or TrackerConfig()
        self.roi = roi                          # (x1, y1, x2, y2) in frame pixels, or None
        self.tracks: list[Track] = []
        self.events: list[dict[str, Any]] = []
        self.events_dropped = 0
        self._next_id = 1
        self.frames = 0
        self.stats = {"created": 0, "confirmed": 0, "lost": 0, "recovered": 0, "terminated": 0,
                      "class_changes": 0, "enter_roi": 0, "exit_roi": 0, "low_conf_ignored": 0,
                      "max_concurrent_confirmed": 0, "tentative_dropped": 0}
        self._lengths: list[float] = []

    # ---------------------------------------------------------------- events
    def _event(self, kind: str, track: Track, t: float, frame: int, *, stat: str | None = None, **detail) -> None:
        key = stat or kind
        if key in self.stats:
            self.stats[key] += 1
        if len(self.events) >= self.config.max_events:
            self.events_dropped += 1
            return
        self.events.append({"t": round(t, 3), "frame": frame, "track_id": track.id, "event": kind,
                            "family": track.family, "conf": round(track.conf_ema, 3), **detail})

    # ---------------------------------------------------------------- classification
    def _vote(self, track: Track, family: str | None, conf: float, t: float, frame: int) -> None:
        a = self.config.ema_alpha
        for key in list(track.votes):
            track.votes[key] *= (1.0 - a)
        if family:
            track.votes[family] = track.votes.get(family, 0.0) + a * max(conf, 1e-3)
        if not track.votes:
            return
        best = max(track.votes, key=track.votes.get)
        if track.family is None:
            track.family = best
        elif best != track.family and track.votes[best] - track.votes.get(track.family, 0.0) >= self.config.class_switch_margin:
            previous = track.family
            track.family = best
            track.class_changes += 1
            if track.state != "tentative":
                self._event("class_change", track, t, frame, stat="class_changes", from_family=previous, to_family=best)

    def _roi_check(self, track: Track, t: float, frame: int) -> None:
        if self.roi is None or track.state != "confirmed":
            return
        cx, cy = _center(track.xyxy)
        inside = self.roi[0] <= cx <= self.roi[2] and self.roi[1] <= cy <= self.roi[3]
        if track.in_roi is None:
            track.in_roi = inside
            return
        if inside != track.in_roi:
            track.in_roi = inside
            self._event("enter_roi" if inside else "exit_roi", track, t, frame)

    # ---------------------------------------------------------------- association
    def _cost(self, track: Track, det: dict[str, Any], t: float) -> float | None:
        cfg = self.config
        lost = track.state == "lost"
        pred = track.predicted(t, cfg.max_extrapolate_s)
        box = det["xyxy"]
        overlap = iou(pred, box)
        (pcx, pcy), (dcx, dcy) = _center(pred), _center(box)
        dist = math.hypot(pcx - dcx, pcy - dcy) / _diag(pred)
        gate = cfg.lost_center_gate if lost else (cfg.new_center_gate if track.hits < 2 else cfg.center_gate)
        if overlap < cfg.min_iou and dist > gate:
            return None
        family, conf = det.get("family_code"), float(det.get("confidence") or 0.0)
        if track.family and family and family != track.family and conf >= cfg.min_create_conf:
            # Two confident different classes: only the same object if they still overlap strongly.
            if overlap < 0.5:
                return None
        proximity = max(0.0, 1.0 - dist / max(gate, 1e-6))
        return 1.0 - (overlap + 0.5 * proximity) / 1.5

    def update(self, detections: list[dict[str, Any]], *, t: float, frame: int) -> list[int | None]:
        """Associates this frame's detections (xyxy, confidence, family_code) to tracks.
        Returns one track_id per detection (None for a low-confidence detection that matched nothing)."""
        cfg = self.config
        self.frames += 1
        candidates = [tr for tr in self.tracks]
        assigned: dict[int, Track] = {}
        if candidates and detections:
            costs = [[self._cost(tr, det, t) for tr in candidates] for det in detections]
            big = 1e6
            matrix = [[c if c is not None else big for c in row] for row in costs]
            if linear_sum_assignment is not None:
                rows, cols = linear_sum_assignment(matrix)
                pairs = list(zip(rows.tolist(), cols.tolist()))
            else:   # greedy fallback on the same gated costs
                flat = sorted((matrix[d][k], d, k) for d in range(len(detections)) for k in range(len(candidates)))
                used_d, used_k, pairs = set(), set(), []
                for c, d, k in flat:
                    if d not in used_d and k not in used_k:
                        used_d.add(d)
                        used_k.add(k)
                        pairs.append((d, k))
            for d, k in pairs:
                if matrix[d][k] < big:
                    assigned[d] = candidates[k]
        matched_tracks = set()
        ids: list[int | None] = []
        for d, det in enumerate(detections):
            box = [float(v) for v in det["xyxy"][:4]]
            conf = float(det.get("confidence") or 0.0)
            family = det.get("family_code")
            track = assigned.get(d)
            if track is None:
                if conf < cfg.min_create_conf:
                    self.stats["low_conf_ignored"] += 1
                    ids.append(None)
                    continue
                track = Track(id=self._next_id, xyxy=box, first_t=t, last_t=t, first_frame=frame, last_frame=frame,
                              conf_ema=conf, max_conf=conf)
                self._next_id += 1
                self.tracks.append(track)
                self.stats["created"] += 1
                self._vote(track, family, conf, t, frame)
                if cfg.min_hits <= 1:
                    track.state = "confirmed"
                    self._event("confirmed", track, t, frame)
            else:
                dt = max(t - track.last_t, 1e-6)
                (ocx, ocy), (ncx, ncy) = _center(track.xyxy), _center(box)
                va = cfg.velocity_alpha
                track.velocity = (va * (ncx - ocx) / dt + (1 - va) * track.velocity[0],
                                  va * (ncy - ocy) / dt + (1 - va) * track.velocity[1])
                if track.state == "lost":
                    track.state = "confirmed"
                    track.recoveries += 1
                    self._event("recovered", track, t, frame, gap_s=round(t - track.last_t, 3))
                track.xyxy, track.last_t, track.last_frame = box, t, frame
                track.hits += 1
                track.conf_ema = cfg.ema_alpha * conf + (1 - cfg.ema_alpha) * track.conf_ema
                track.max_conf = max(track.max_conf, conf)
                self._vote(track, family, conf, t, frame)
                if track.state == "tentative" and track.hits >= cfg.min_hits:
                    track.state = "confirmed"
                    self._event("confirmed", track, t, frame)
            matched_tracks.add(track.id)
            self._roi_check(track, t, frame)
            ids.append(track.id)
        # unmatched tracks: tentative ones die quickly, confirmed ones become lost, lost ones are forgotten
        survivors = []
        for track in self.tracks:
            if track.id in matched_tracks:
                survivors.append(track)
                continue
            age = t - track.last_t
            if track.state == "tentative":
                if age > cfg.lost_after_s:
                    self.stats["tentative_dropped"] += 1
                    continue
            elif track.state == "confirmed" and age > cfg.lost_after_s:
                track.state = "lost"
                self._event("lost", track, t, frame, last_seen_t=round(track.last_t, 3))
            elif track.state == "lost" and age > cfg.forget_after_s:
                self._event("terminated", track, t, frame, last_seen_t=round(track.last_t, 3))
                self._lengths.append(track.last_t - track.first_t)
                continue
            survivors.append(track)
        self.tracks = survivors
        concurrent = sum(1 for tr in self.tracks if tr.state == "confirmed" and tr.id in matched_tracks)
        self.stats["max_concurrent_confirmed"] = max(self.stats["max_concurrent_confirmed"], concurrent)
        return ids

    # ---------------------------------------------------------------- summary
    def active(self) -> list[Track]:
        return [tr for tr in self.tracks if tr.state == "confirmed"]

    def summary(self, *, final_frame_total: int | None = None, include_events: bool = False) -> dict[str, Any]:
        lengths = self._lengths + [tr.last_t - tr.first_t for tr in self.tracks if tr.state != "tentative"]
        unique_confirmed = self.stats["confirmed"]
        out = {
            "version": TRACKING_VERSION,
            "frames": self.frames,
            "unique_confirmed_tracks": unique_confirmed,
            "active_confirmed_tracks": len(self.active()),
            "lost_now": sum(1 for tr in self.tracks if tr.state == "lost"),
            "stats": dict(self.stats),
            "mean_track_seconds": round(sum(lengths) / len(lengths), 3) if lengths else 0.0,
            "roi": [round(v, 1) for v in self.roi] if self.roi else None,
            "config": self.config.public(),
            "events_recorded": len(self.events),
            "events_dropped": self.events_dropped,
            # Traceability only: tracks are never a count. A difference here is a signal for review.
            "note": "Tracking statistics are not counts; the final count is the last valid frame (MS-03.1).",
        }
        if final_frame_total is not None:
            out["final_frame_total"] = int(final_frame_total)
            out["active_tracks_match_final_frame"] = len(self.active()) == int(final_frame_total)
        if include_events:
            out["events"] = list(self.events)
        return out
