"""PEF Vision Worker — binds to OP vision sessions, runs YOLO on uploaded video."""

from __future__ import annotations

import hashlib
import threading
import uuid
from pathlib import Path

from flask import Flask, jsonify, request
from flask_sock import Sock
from live_ws import frame_activity, live_connections, register_live_ws, revoke_user

from config import (
    ASSIGN_SOLVER,
    BOX_RECALL_CONF,
    CONF_DISPLAY_REASSIGN,
    CONF_DISPLAY_THRESHOLD,
    COUNT_SAMPLE_EVERY,
    FRAME_STRIDE,
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
    VIDEO_SUFFIXES,
    WEIGHTS_PATH,
    WORKER_PIPELINE,
)
from inference import (
    SessionState,
    analyze_first_frame,
    get_model,
    process_video_file,
    read_timeline_samples,
)

from telemetry import TELEMETRY
from model_store import MODEL_CACHE, ModelUnavailable, bundled_sha256, parse_spec
from bind_auth import ALLOW_UNPINNED_BIND, verify_bind_signature

app = Flask(__name__)
sock = Sock(app)
TELEMETRY.start()   # DS06 session_telemetry flush thread (no-op without MONGO_URL)

_sessions: dict[str, SessionState] = {}
_lock = threading.Lock()
_jobs: dict[str, threading.Thread] = {}
# A session with one of these states is running a job: no new upload or run until it ends.
BUSY_STATUSES = {"loading", "processing"}


def _get_state(session_id: str) -> SessionState | None:
    with _lock:
        return _sessions.get(session_id)


@app.get("/activity")
def activity():
    """MS-05: last valid live camera frame per session (MonitoringService collector, internal network only).

    Session ids and timestamps only: no frames, users or tickets. Not published by the API Gateway."""
    return jsonify({"ok": True, "sessions": frame_activity()})


@app.get("/health")
def health():
    weights_ok = WEIGHTS_PATH.is_file()
    return jsonify({
        "ok": True,
        "ws_path": "/ws",
        "weights_path": str(WEIGHTS_PATH),
        "weights_present": weights_ok,
        # MS-02: verified models in memory (by SHA-256) and the bundled file's own SHA-256.
        "loaded_models": MODEL_CACHE.loaded(),
        "bundled_sha256": bundled_sha256() if weights_ok else None,
        "unpinned_bind_allowed": ALLOW_UNPINNED_BIND,
        "live_connections": live_connections(),
        "bound_sessions": len(_sessions),
        "pipeline": WORKER_PIPELINE,
        "assign_solver": ASSIGN_SOLVER,
        "box_recall_conf": BOX_RECALL_CONF,
        "conf_display_threshold": CONF_DISPLAY_THRESHOLD,
        "conf_display_reassign": CONF_DISPLAY_REASSIGN,
        "reassign_min_score": REASSIGN_MIN_SCORE,
        "reassign_max_gap": REASSIGN_MAX_GAP,
        "frame_stride": FRAME_STRIDE,
        "hold_seconds": HOLD_SECONDS,
        "name_hold_seconds": NAME_HOLD_SECONDS,
        "hold_min_score": HOLD_MIN_SCORE,
        "hold_absent_seconds": HOLD_ABSENT_SECONDS,
        "hold_absent_streak": HOLD_ABSENT_STREAK,
        "hold_seed_frames": HOLD_SEED_FRAMES,
        "hold_zone_iou": HOLD_ZONE_IOU,
        "count_sample_every": COUNT_SAMPLE_EVERY,
        "telemetry": {"enabled": TELEMETRY.enabled, "pending": len(TELEMETRY.pending()), **TELEMETRY.stats},
    })


def _model_error(exc: ModelUnavailable):
    return jsonify({"ok": False, "error": exc.message, "code": exc.code}), exc.status


@app.post("/auth/revoke")
def revoke_live_connections():
    """MS-03: the portal (signed like the binds) revokes every open live WebSocket of a user at once, e.g.
    after deactivation, password reset or sign-out. Each connection stops inferring and closes within ~1 s."""
    if not verify_bind_signature(request.get_data(), request.headers.get("X-PEF-Signature")):
        return jsonify({"ok": False, "error": "Signature missing or invalid", "code": "unauthorized"}), 401
    user_id = str((request.get_json(silent=True) or {}).get("user_id") or "").strip()
    if not user_id:
        return jsonify({"ok": False, "error": "user_id is required", "code": "invalid_request"}), 400
    return jsonify({"ok": True, "revoked_connections": revoke_user(user_id)})


@app.post("/sessions/<session_id>/bind")
def bind_session(session_id: str):
    # MS-02: the bind decides which weights a session infers with, so it must come from the portal
    # (HMAC over the raw body with VISION_WS_SECRET) and must name the session's pinned model.
    if not verify_bind_signature(request.get_data(), request.headers.get("X-PEF-Signature")):
        return jsonify({"ok": False, "error": "Bind signature missing or invalid", "code": "unauthorized"}), 401
    payload = request.get_json(silent=True) or {}
    spec = None
    if payload.get("model") is not None or not ALLOW_UNPINNED_BIND:
        try:
            spec = parse_spec(payload.get("model"))
        except ModelUnavailable as exc:
            return _model_error(exc)
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
    if spec is not None and prev is not None and prev.model_sha256 and prev.model_sha256 != spec.sha256:
        # A bound session never changes weights (an Administration activation must not swap them mid-session).
        return jsonify({"ok": False, "code": "model_conflict",
                        "error": "This session is already bound to other model weights"}), 409
    model_info = None
    if spec is not None:
        try:
            model_info = MODEL_CACHE.prepare(spec, class_map.keys())
        except ModelUnavailable as exc:
            return _model_error(exc)
        except Exception as exc:  # noqa: BLE001 - a weights file that does not load is reported, not raised
            return jsonify({"ok": False, "code": "model_load_failed",
                            "error": f"The model weights could not be loaded: {type(exc).__name__}"}), 422
    camera = _camera_config(payload.get("camera"))
    with _lock:
        prev = _sessions.get(session_id)
        state = prev or SessionState(session_id=session_id)
        state.expected = expected
        state.class_map = class_map
        state.camera = camera
        if spec is not None:
            state.model_id, state.model_sha256 = spec.model_id, spec.sha256
            state.model_version = spec.model_version or state.model_version
            state.model_source = model_info["source"]
        else:
            state.model_version = str(payload.get("model_version") or state.model_version)
        state.conf_threshold = float(payload.get("conf_threshold") or CONF_DISPLAY_THRESHOLD)
        # Re-binding (every capture page load) never drops an uploaded video or its analyses.
        if state.status not in BUSY_STATUSES and state.video_id is None:
            state.status = "idle"
            state.message = "Bound; waiting for video"
        _sessions[session_id] = state
    if spec is None:
        # Legacy unpinned bind (tests / development only): warm the bundled model in background.
        threading.Thread(target=lambda: get_model(), daemon=True).start()
    return jsonify({"ok": True, "model": model_info, "session": state.to_public()})


@app.post("/sessions/<session_id>/unbind")
def unbind_session(session_id: str):
    with _lock:
        state = _sessions.pop(session_id, None)
        if state:
            state.cancel = True
            _discard_video(state)
    return jsonify({"ok": True, "unbound": bool(state)})


def _camera_config(raw) -> dict:
    """MS-11 (R08): station camera configuration from the signed bind. Only known, typed fields are kept;
    a malformed value is dropped (the run continues without it), never trusted."""
    if not isinstance(raw, dict):
        return {}
    out = {}
    for key in ("id", "name", "device_type"):
        if raw.get(key) is not None:
            out[key] = str(raw[key])[:120]
    for key, low, high in (("resolution_width", 160, 7680), ("resolution_height", 120, 4320), ("fps", 1, 120)):
        try:
            value = int(raw[key])
        except (KeyError, TypeError, ValueError):
            continue
        if low <= value <= high:
            out[key] = value
    roi = raw.get("roi")
    if isinstance(roi, dict):
        try:
            clean = {k: int(roi[k]) for k in ("x", "y", "w", "h", "frame_w", "frame_h")}
        except (KeyError, TypeError, ValueError):
            clean = None
        if clean and clean["w"] > 0 and clean["h"] > 0 and clean["x"] >= 0 and clean["y"] >= 0:
            out["roi"] = clean
    out["simulated"] = bool(raw.get("simulated"))
    # MS-12: technician-measured calibration (mm/pixel of the frame it was measured on). Informational: shown with the
    # tracking summary; it never changes a detection or a count.
    cal = raw.get("calibration")
    if isinstance(cal, dict):
        try:
            mm = float(cal["mm_per_pixel"])
            fw, fh = int(cal["frame_width"]), int(cal["frame_height"])
        except (KeyError, TypeError, ValueError):
            mm = None
        if mm is not None and 0 < mm < 1000 and 160 <= fw <= 7680 and 120 <= fh <= 4320:
            out["calibration"] = {"id": str(cal.get("id") or "")[:36], "mm_per_pixel": mm,
                                  "frame_width": fw, "frame_height": fh}
    return out


@app.get("/sessions/<session_id>/tracking")
def session_tracking(session_id: str):
    """MS-11 (R11): tracking summary and events of the current / last run (traceability; never a count)."""
    state = _get_state(session_id)
    if state is None:
        return jsonify({"ok": False, "error": "Session not bound"}), 404
    tracker = state.tracker
    if tracker is None:
        return jsonify({"ok": True, "run_id": state.run_id, "tracking": state.tracking or None, "events": [],
                        "camera_calibration": (state.camera or {}).get("calibration")})
    total = (state.final_frame or {}).get("total") if (state.final_frame or {}).get("ok") else None
    data = tracker.summary(final_frame_total=total, include_events=True)
    events = data.pop("events")
    try:
        offset = max(0, int(request.args.get("offset", 0)))
        limit = min(500, max(1, int(request.args.get("limit", 200))))
    except ValueError:
        return jsonify({"ok": False, "error": "offset and limit must be integers"}), 400
    return jsonify({"ok": True, "run_id": state.run_id, "tracking": data, "events": events[offset:offset + limit],
                    "events_total": len(events), "camera_calibration": (state.camera or {}).get("calibration")})


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
        "count_sample_every": (
            state.count_sample_every if state else COUNT_SAMPLE_EVERY
        ),
        "samples": samples,
        "sample_count": len(samples),
    })


@app.post("/sessions/<session_id>/process-video")
def process_video(session_id: str):
    """Legacy one-shot upload + full run (kept for compatibility; the portal uses /video)."""
    state = _get_state(session_id)
    if state is None:
        return jsonify({"ok": False, "error": "Session not bound. Start a vision session first."}), 409
    if state.status in BUSY_STATUSES:
        return jsonify({"ok": False, "error": "Already processing a video for this session."}), 409

    upload = request.files.get("video")
    if upload is None or not upload.filename:
        return jsonify({"ok": False, "error": "Missing video file field 'video'."}), 400

    suffix = Path(upload.filename).suffix.lower() or ".mp4"
    if suffix not in VIDEO_SUFFIXES:
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


def _discard_video(state: SessionState) -> None:
    """Remove the stored video of the session (new upload, unbind, or run completed)."""
    if state.video_path:
        try:
            Path(state.video_path).unlink(missing_ok=True)
        except OSError:
            pass
    state.video_path = None


@app.post("/sessions/<session_id>/video")
def upload_video(session_id: str):
    """Single-video flow, step 1: store the video and analyse ONLY its first valid frame.

    Nothing else runs: the full processing starts with POST .../video/<video_id>/process, which the
    portal sends only after the Operator registered the initial count.
    """
    upload = request.files.get("video")
    if upload is None or not upload.filename:
        return jsonify({"ok": False, "error": "Missing video file field 'video'."}), 400
    suffix = Path(upload.filename).suffix.lower() or ".mp4"
    if suffix not in VIDEO_SUFFIXES:
        return jsonify({
            "ok": False,
            "error": f"Unsupported format {suffix}. Use mp4, mkv, avi, mov, webm or m4v.",
        }), 400
    with _lock:
        state = _sessions.get(session_id)
        if state is None:
            return jsonify({"ok": False, "error": "Session not bound. Start a vision session first."}), 409
        if state.status in BUSY_STATUSES:
            return jsonify({"ok": False, "error": "The current video is still being analysed."}), 409
        state.status = "loading"  # claims the session: a concurrent upload gets 409

    video_id = uuid.uuid4().hex
    dest = UPLOAD_DIR / f"{session_id}_{video_id}{suffix}"
    digest = hashlib.sha256()
    size = 0
    try:
        with dest.open("wb") as fh:
            for chunk in iter(lambda: upload.stream.read(1 << 20), b""):
                digest.update(chunk)
                size += len(chunk)
                fh.write(chunk)
    except OSError as exc:
        dest.unlink(missing_ok=True)
        state.status, state.error_stage = "error", "first_frame"
        state.last_error = state.message = f"Could not store the video: {exc}"
        return jsonify({"ok": False, "error": state.last_error}), 500
    if size == 0:
        dest.unlink(missing_ok=True)
        state.status = "idle"
        return jsonify({"ok": False, "error": "The video file is empty."}), 400

    with _lock:
        _discard_video(state)
        state.cancel = False
        state.video_id = video_id
        state.video_path = str(dest)
        state.video_meta = {"filename": Path(upload.filename).name[:200], "sha256": digest.hexdigest(),
                            "size_bytes": size, "suffix": suffix}
        state.initial_frame = None
        state.final_frame = None
        state.run_id = None
        state.progress = 0.0
        state.frames_processed = 0
        state.frame_index = 0
        state.counts, state.raw_counts, state.boxes, state.out_of_kit = {}, {}, [], []
        state.category_summary, state.assignment_metrics = {}, {}
        state.frame_jpeg_b64 = None
    thread = threading.Thread(target=analyze_first_frame, args=(str(dest), state), daemon=True,
                              name=f"yolo-first-{session_id[:8]}")
    with _lock:
        _jobs[session_id] = thread
    thread.start()
    return jsonify({"ok": True, "video_id": video_id, "session": state.to_public()}), 202


@app.post("/sessions/<session_id>/video/<video_id>/process")
def process_uploaded_video(session_id: str, video_id: str):
    """Single-video flow, step 3: full run of the SAME stored video, then its last valid frame."""
    with _lock:
        state = _sessions.get(session_id)
        if state is None:
            return jsonify({"ok": False, "error": "Session not bound."}), 409
        if state.video_id != video_id:
            return jsonify({"ok": False, "error": "This video is no longer available. Upload it again."}), 404
        if state.status in BUSY_STATUSES:
            return jsonify({"ok": False, "error": "Already processing a video for this session."}), 409
        if state.status == "ready" and (state.final_frame or {}).get("ok"):
            # Duplicate request after the run: same result, no second run.
            return jsonify({"ok": True, "already_processed": True, "session": state.to_public()}), 200
        if not state.video_path or not Path(state.video_path).is_file():
            return jsonify({"ok": False, "error": "This video is no longer available. Upload it again."}), 404
        if not (state.initial_frame or {}).get("ok"):
            return jsonify({"ok": False, "error": "The first frame of this video was not analysed."}), 409
        state.status = "processing"  # claims the session: a second click gets 409 or the run above
        path = state.video_path

    def _run():
        process_video_file(path, state)
        if state.status == "ready":
            with _lock:
                _discard_video(state)  # no longer needed: the edge frames are analysed

    thread = threading.Thread(target=_run, daemon=True, name=f"yolo-{session_id[:8]}")
    with _lock:
        _jobs[session_id] = thread
    thread.start()
    return jsonify({"ok": True, "video_id": video_id, "session": state.to_public()}), 202


@app.delete("/sessions/<session_id>/video")
def discard_video(session_id: str):
    """Drop the uploaded video and its analyses (e.g. not the video of the registered initial count)."""
    with _lock:
        state = _sessions.get(session_id)
        if state is None:
            return jsonify({"ok": False, "error": "Session not bound"}), 404
        if state.status in BUSY_STATUSES:
            return jsonify({"ok": False, "error": "The current video is still being analysed."}), 409
        _discard_video(state)
        state.video_id, state.video_meta = None, {}
        state.initial_frame = state.final_frame = None
        state.status, state.message, state.error_stage = "idle", "Bound; waiting for video", None
        state.frame_jpeg_b64 = None
    return jsonify({"ok": True, "session": state.to_public()})


@app.post("/sessions/<session_id>/cancel")
def cancel_job(session_id: str):
    state = _get_state(session_id)
    if state is None:
        return jsonify({"ok": False, "error": "Session not bound"}), 404
    state.cancel = True
    return jsonify({"ok": True})


register_live_ws(sock, get_state=_get_state)


if __name__ == "__main__":
    print(f"VisionWorker on http://{HOST}:{PORT}")
    print(f"Weights: {WEIGHTS_PATH} (present={WEIGHTS_PATH.is_file()})")
    app.run(host=HOST, port=PORT, debug=False, threaded=True)
