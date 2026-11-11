"""IoU tracks + EMA of class scores. Rollback: VISION_TRACK_MEMORY=0."""
from __future__ import annotations

from typing import Any

from config import TRACK_EMA_ALPHA, TRACK_IOU, TRACK_MEMORY, TRACK_MISS_SECONDS


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


def update_box_tracks(
    tracks: list[dict[str, Any]],
    boxes: list[dict[str, Any]],
    *,
    video_t: float,
    enabled: bool = TRACK_MEMORY,
    min_iou: float = TRACK_IOU,
    alpha: float = TRACK_EMA_ALPHA,
    miss_s: float = TRACK_MISS_SECONDS,
) -> list[dict[str, Any]]:
    """Associate live boxes to tracks; write ema_scores + prev_family onto each box."""
    if not enabled:
        for box in boxes:
            box.pop("ema_scores", None)
            box.pop("prev_family", None)
            box.pop("track_id", None)
        return []

    pairs: list[tuple[float, int, int]] = []
    for ti, tr in enumerate(tracks):
        txy = tr.get("xyxy")
        if not txy:
            continue
        for bi, box in enumerate(boxes):
            iou = _xyxy_iou(txy, box.get("xyxy") or [])
            if iou >= min_iou:
                pairs.append((iou, ti, bi))
    pairs.sort(reverse=True)
    used_t: set[int] = set()
    used_b: set[int] = set()
    match: dict[int, int] = {}
    for _iou, ti, bi in pairs:
        if ti in used_t or bi in used_b:
            continue
        used_t.add(ti)
        used_b.add(bi)
        match[bi] = ti

    next_tracks: list[dict[str, Any]] = []
    for bi, box in enumerate(boxes):
        scores = [float(v) for v in (box.get("scores_by_class") or [])]
        ti = match.get(bi)
        if ti is None:
            track = {
                "xyxy": list(box.get("xyxy") or []),
                "ema_scores": scores,
                "family_code": None,
                "last_t": float(video_t),
            }
        else:
            prev = tracks[ti]
            old = list(prev.get("ema_scores") or [])
            if old and scores and len(old) == len(scores):
                ema = [alpha * s + (1.0 - alpha) * o for s, o in zip(scores, old)]
            else:
                ema = scores or old
            track = {
                "xyxy": list(box.get("xyxy") or []),
                "ema_scores": ema,
                "family_code": prev.get("family_code"),
                "last_t": float(video_t),
            }
        box["ema_scores"] = list(track.get("ema_scores") or [])
        box["prev_family"] = track.get("family_code")
        box["track_id"] = len(next_tracks)
        track["id"] = box["track_id"]
        next_tracks.append(track)

    for ti, tr in enumerate(tracks):
        if ti in used_t:
            continue
        last_t = float(tr.get("last_t") or video_t)
        if (float(video_t) - last_t) <= miss_s:
            next_tracks.append(tr)
    return next_tracks


def commit_track_families(
    tracks: list[dict[str, Any]],
    refined_boxes: list[dict[str, Any]],
    *,
    enabled: bool = TRACK_MEMORY,
) -> None:
    if not enabled or not tracks:
        return
    by_id: dict[int, str | None] = {}
    for box in refined_boxes:
        tid = box.get("track_id")
        if tid is None:
            continue
        try:
            code = box.get("family_code")
            by_id[int(tid)] = str(code) if code else None
        except (TypeError, ValueError):
            continue
    for tr in tracks:
        tid = tr.get("id")
        if tid in by_id:
            tr["family_code"] = by_id[tid]
