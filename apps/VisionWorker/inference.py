"""YOLO inference + expected-inventory filter for the demo vision worker."""

from __future__ import annotations

import base64
import threading
from dataclasses import dataclass, field
from typing import Any

import cv2
import numpy as np

from config import CONF_DISPLAY_THRESHOLD, FRAME_STRIDE, MAX_DET, WEIGHTS_PATH

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
    model_version: str = "yolo26l-demo"
    conf_threshold: float = CONF_DISPLAY_THRESHOLD
    frame_index: int = 0
    frames_processed: int = 0
    cancel: bool = False
    last_error: str | None = None

    def to_public(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "status": self.status,
            "message": self.message,
            "progress": self.progress,
            "frame_jpeg_b64": self.frame_jpeg_b64,
            "boxes": self.boxes,
            "counts": self.counts,
            "model_version": self.model_version,
            "conf_threshold": self.conf_threshold,
            "frame_index": self.frame_index,
            "frames_processed": self.frames_processed,
            "last_error": self.last_error,
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
        conf = box.get("confidence", 0.0)
        color = (56, 189, 248) if box.get("high_confidence") else (251, 191, 36)
        cv2.rectangle(out, (x1, y1), (x2, y2), color, 2)
        text = f"{label} {conf:.2f}"
        (tw, th), _ = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, 0.55, 1)
        cv2.rectangle(out, (x1, max(0, y1 - th - 8)), (x1 + tw + 6, y1), color, -1)
        cv2.putText(
            out, text, (x1 + 3, y1 - 4),
            cv2.FONT_HERSHEY_SIMPLEX, 0.55, (15, 23, 42), 1, cv2.LINE_AA,
        )
    return out


def _encode_jpeg_b64(frame_bgr: np.ndarray) -> str:
    ok, buf = cv2.imencode(".jpg", frame_bgr, [int(cv2.IMWRITE_JPEG_QUALITY), 82])
    if not ok:
        raise RuntimeError("Failed to encode JPEG frame")
    return base64.b64encode(buf.tobytes()).decode("ascii")


def filter_detections(
    raw_boxes: list[dict[str, Any]],
    *,
    expected: dict[str, dict[str, Any]],
    class_map: dict[int, dict[str, Any]],
    conf_threshold: float,
) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    """Keep only classes in expected inventory; low-conf boxes show category type as label."""
    filtered: list[dict[str, Any]] = []
    tallies: dict[str, list[float]] = {code: [] for code in expected}

    for box in raw_boxes:
        class_id = int(box["class_id"])
        meta = class_map.get(class_id)
        if meta is None:
            continue
        family_code = meta["family_code"]
        if family_code not in expected:
            continue
        conf = float(box["confidence"])
        high = conf >= conf_threshold
        label = meta["family_name"] if high else (meta.get("category_name") or "Type")
        filtered.append({
            "xyxy": box["xyxy"],
            "class_id": class_id,
            "family_code": family_code,
            "family_id": meta.get("family_id"),
            "family_name": meta.get("family_name"),
            "category_name": meta.get("category_name"),
            "confidence": conf,
            "high_confidence": high,
            "label": label,
        })
        tallies[family_code].append(conf)

    counts: dict[str, dict[str, Any]] = {}
    for code, confs in tallies.items():
        exp = expected[code]
        detected = len(confs)
        counts[code] = {
            "family_id": exp.get("family_id"),
            "family_code": code,
            "family_name": exp.get("family_name"),
            "category_name": exp.get("category_name"),
            "expected_quantity": exp.get("expected_quantity", 0),
            "detected_quantity": detected,
            "avg_confidence": (sum(confs) / detected) if detected else None,
            "difference": detected - int(exp.get("expected_quantity") or 0),
        }
    return filtered, counts


def run_frame(
    frame_bgr: np.ndarray,
    state: SessionState,
) -> None:
    model = get_model()
    results = model.predict(
        source=frame_bgr,
        conf=0.15,
        max_det=MAX_DET,
        verbose=False,
    )
    raw: list[dict[str, Any]] = []
    if results:
        r0 = results[0]
        names = r0.names or {}
        if r0.boxes is not None:
            for b in r0.boxes:
                cls_id = int(b.cls.item())
                conf = float(b.conf.item())
                xyxy = [float(v) for v in b.xyxy.cpu().numpy().reshape(-1)]
                # Prefer seeded class_map; fall back to model.names string match later in map.
                raw.append({
                    "class_id": cls_id,
                    "confidence": conf,
                    "xyxy": xyxy,
                    "model_name": names.get(cls_id, str(cls_id)),
                })

    # If class_map keys miss but names match family_name, enrich map on the fly.
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

    boxes, counts = filter_detections(
        raw,
        expected=state.expected,
        class_map=state.class_map,
        conf_threshold=state.conf_threshold,
    )
    annotated = _draw_boxes(frame_bgr, boxes)
    state.boxes = boxes
    state.counts = counts
    state.frame_jpeg_b64 = _encode_jpeg_b64(annotated)
    state.frames_processed += 1


def process_video_file(path: str, state: SessionState) -> None:
    state.status = "processing"
    state.message = "Processing video"
    state.progress = 0.0
    state.cancel = False
    state.last_error = None
    cap = cv2.VideoCapture(path)
    if not cap.isOpened():
        state.status = "error"
        state.last_error = "Could not open video file"
        state.message = state.last_error
        return
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    idx = 0
    try:
        while True:
            if state.cancel:
                state.status = "idle"
                state.message = "Cancelled"
                break
            ok, frame = cap.read()
            if not ok:
                break
            state.frame_index = idx
            if idx % FRAME_STRIDE == 0:
                run_frame(frame, state)
            if total > 0:
                state.progress = min(0.99, (idx + 1) / total)
            idx += 1
        if state.status == "processing":
            state.progress = 1.0
            state.status = "ready"
            state.message = "Video processing complete"
    except Exception as exc:  # noqa: BLE001 — surface to UI
        state.status = "error"
        state.last_error = str(exc)
        state.message = f"Inference error: {exc}"
    finally:
        cap.release()
