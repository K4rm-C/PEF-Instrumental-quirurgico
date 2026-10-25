"""PEF Vision Worker — binds to OP vision sessions, runs YOLO on uploaded video."""

from __future__ import annotations

import threading
import uuid
from pathlib import Path

from flask import Flask, jsonify, request
from flask_sock import Sock

from config import (
    ASSIGN_SOLVER,
    BOX_RECALL_CONF,
    CONF_DISPLAY_REASSIGN,
    CONF_DISPLAY_THRESHOLD,
    COUNT_SAMPLE_INTERVAL_S,
    FRAME_STRIDE_CPU,
    FRAME_STRIDE_CUDA,
    HOLD_ABSENT_SECONDS,
    HOLD_ABSENT_STREAK,
    HOLD_MIN_SCORE,
    HOLD_SECONDS,
    HOLD_SEED_FRAMES,
    HOLD_ZONE_IOU,
    HOST,
    NAME_HOLD_SECONDS,
    PORT,
    REASSIGN_MAX_GAP,
    REASSIGN_MIN_SCORE,
    UPLOAD_DIR,
    WEIGHTS_PATH,
    WORKER_PIPELINE,
)
from inference import (
    SessionState,
    cancel_capture,
    device_info,
    effective_frame_stride,
    get_model,
    process_video_file,
    read_timeline_samples,
)
from live_ws import register_live_ws

app = Flask(__name__)
sock = Sock(app)

_sessions: dict[str, SessionState] = {}
_lock = threading.Lock()
_jobs: dict[str, threading.Thread] = {}


def _get_state(session_id: str) -> SessionState | None:
    with _lock:
        return _sessions.get(session_id)


@app.get("/health")
def health():
    weights_ok = WEIGHTS_PATH.is_file()
    info = device_info()
    return jsonify({
        "ok": True,
        "weights_path": str(WEIGHTS_PATH),
        "weights_present": weights_ok,
        "cuda_available": info["cuda_available"],
        "cuda_device_name": info["cuda_device_name"],
        "model_loaded": info["model_loaded"],
        "model_device": info["model_device"],
        "bound_sessions": len(_sessions),
        "pipeline": WORKER_PIPELINE,
        "assign_solver": ASSIGN_SOLVER,
        "box_recall_conf": BOX_RECALL_CONF,
        "conf_display_threshold": CONF_DISPLAY_THRESHOLD,
        "conf_display_reassign": CONF_DISPLAY_REASSIGN,
        "reassign_min_score": REASSIGN_MIN_SCORE,
        "reassign_max_gap": REASSIGN_MAX_GAP,
        "frame_stride": info["frame_stride"],
        "frame_stride_cuda": FRAME_STRIDE_CUDA,
        "frame_stride_cpu": FRAME_STRIDE_CPU,
        "hold_seconds": HOLD_SECONDS,
        "name_hold_seconds": NAME_HOLD_SECONDS,
        "hold_min_score": HOLD_MIN_SCORE,
        "hold_absent_seconds": HOLD_ABSENT_SECONDS,
        "hold_absent_streak": HOLD_ABSENT_STREAK,
        "hold_seed_frames": HOLD_SEED_FRAMES,
        "hold_zone_iou": HOLD_ZONE_IOU,
        "count_sample_interval_s": COUNT_SAMPLE_INTERVAL_S,
        "ws_path": "/ws",
    })


@app.post("/sessions/<session_id>/bind")
def bind_session(session_id: str):
    payload = request.get_json(silent=True) or {}
    expected_list = payload.get("expected") or []
    class_map_list = payload.get("class_map") or []
    expected = {}
    for row in expected_list:
        code = str(row.get("family_code") or "").strip()
        if not code:
            continue
        expected[code] = {
            "family_id": row.get("family_id"),
            "family_code": code,
            "family_name": row.get("family_name") or code,
            "category_name": row.get("category_name") or "Type",
            "expected_quantity": int(row.get("expected_quantity") or 0),
        }
    class_map = {}
    for row in class_map_list:
        try:
            yolo_id = int(row["yolo_class_id"])
        except (KeyError, TypeError, ValueError):
            continue
        class_map[yolo_id] = {
            "family_id": row.get("family_id"),
            "family_code": row.get("family_code"),
            "family_name": row.get("family_name"),
            "category_name": row.get("category_name") or "Type",
        }
    with _lock:
        prev = _sessions.get(session_id)
        state = prev or SessionState(session_id=session_id)
        state.expected = expected
        state.class_map = class_map
        state.model_version = str(payload.get("model_version") or state.model_version)
        state.conf_threshold = float(payload.get("conf_threshold") or CONF_DISPLAY_THRESHOLD)
        if state.status not in {"processing"}:
            state.status = "idle"
            state.message = "Bound; waiting for video"
        _sessions[session_id] = state
    # Warm model in background so first upload is faster.
    threading.Thread(target=lambda: get_model(), daemon=True).start()
    return jsonify({"ok": True, "session": state.to_public()})


@app.post("/sessions/<session_id>/unbind")
def unbind_session(session_id: str):
    with _lock:
        state = _sessions.pop(session_id, None)
        if state:
            state.cancel = True
    return jsonify({"ok": True, "unbound": bool(state)})


@app.get("/sessions/<session_id>/state")
def session_state(session_id: str):
    state = _get_state(session_id)
    if state is None:
        return jsonify({"ok": False, "error": "Session not bound"}), 404
    return jsonify({"ok": True, "session": state.to_public()})


@app.get("/sessions/<session_id>/timeline")
def session_timeline(session_id: str):
    """Sampled tallies along the last (or requested) video run — NDJSON-backed."""
    state = _get_state(session_id)
    run_id = request.args.get("run_id") or (state.run_id if state else None)
    samples = read_timeline_samples(session_id, run_id=run_id)
    return jsonify({
        "ok": True,
        "session_id": session_id,
        "run_id": run_id,
        "count_sample_interval_s": (
            state.count_sample_interval_s if state else COUNT_SAMPLE_INTERVAL_S
        ),
        "frame_stride": (
            state.frame_stride if state else effective_frame_stride(loaded_only=True)
        ),
        "samples": samples,
        "sample_count": len(samples),
    })


@app.post("/sessions/<session_id>/process-video")
def process_video(session_id: str):
    state = _get_state(session_id)
    if state is None:
        return jsonify({"ok": False, "error": "Session not bound. Start a vision session first."}), 409
    if state.status == "processing" and state.capture_source == "file":
        return jsonify({"ok": False, "error": "Already processing a video for this session."}), 409
    if state.status == "processing" and state.capture_source == "live":
        cancel_capture(state)

    upload = request.files.get("video")
    if upload is None or not upload.filename:
        return jsonify({"ok": False, "error": "Missing video file field 'video'."}), 400

    suffix = Path(upload.filename).suffix.lower() or ".mp4"
    if suffix not in {".mp4", ".mkv", ".avi", ".mov", ".webm", ".m4v"}:
        return jsonify({
            "ok": False,
            "error": f"Unsupported format {suffix}. Use mp4, mkv, avi, mov, webm or m4v.",
        }), 400

    job_id = uuid.uuid4().hex
    dest = UPLOAD_DIR / f"{session_id}_{job_id}{suffix}"
    upload.save(dest)

    def _run():
        try:
            process_video_file(str(dest), state)
        finally:
            try:
                dest.unlink(missing_ok=True)
            except OSError:
                pass

    thread = threading.Thread(target=_run, daemon=True, name=f"yolo-{session_id[:8]}")
    with _lock:
        _jobs[session_id] = thread
    thread.start()
    return jsonify({"ok": True, "job_id": job_id, "session": state.to_public()}), 202


@app.post("/sessions/<session_id>/cancel")
def cancel_job(session_id: str):
    state = _get_state(session_id)
    if state is None:
        return jsonify({"ok": False, "error": "Session not bound"}), 404
    cancel_capture(state)
    return jsonify({"ok": True, "session": state.to_public()})


register_live_ws(sock, get_state=_get_state)


if __name__ == "__main__":
    info = device_info()
    print(f"VisionWorker on http://{HOST}:{PORT}")
    print(f"Weights: {WEIGHTS_PATH} (present={WEIGHTS_PATH.is_file()})")
    print(
        f"CUDA: available={info['cuda_available']} "
        f"name={info['cuda_device_name'] or '-'} "
        "(model uses CUDA if available, else CPU)"
    )
    print(
        f"Stride: cuda={FRAME_STRIDE_CUDA} cpu={FRAME_STRIDE_CPU} "
        f"(count sample every {COUNT_SAMPLE_INTERVAL_S:g}s of video)"
    )
    app.run(host=HOST, port=PORT, debug=False, threaded=True)
